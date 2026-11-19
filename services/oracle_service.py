
import re

import oracledb

from config import initialize_oracle_client


class OracleService:
    """Direct Oracle Database access limited to the configured application schema."""

    _IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9_$#]{0,127}$")

    def __init__(self, config):
        self.config = config

    @classmethod
    def _identifier(cls, value, label="Oracle identifier"):
        identifier = str(value).strip().upper()
        if not cls._IDENTIFIER.fullmatch(identifier):
            raise ValueError(f"Invalid {label}: {value}")
        return identifier

    def _validate_connection_settings(self):
        if self.config.DB_SCHEMA != "APPS":
            raise RuntimeError("DB_SCHEMA must be APPS.")
        if self.config.DB_USER.upper() == "APEX_PUBLIC_USER":
            raise RuntimeError(
                "APEX_PUBLIC_USER is not allowed as the application database account."
            )
        missing = [
            name for name in ("DB_HOST", "DB_SERVICE", "DB_USER", "DB_PASSWORD")
            if not getattr(self.config, name, None)
        ]
        if missing:
            raise RuntimeError(
                "Missing Oracle connection settings in .env: "
                + ", ".join(missing)
            )

    def connect(self):
        """Open a connection and set the session's current schema to APPS."""
        self._validate_connection_settings()
        initialize_oracle_client()
        dsn = oracledb.makedsn(
            self.config.DB_HOST,
            self.config.DB_PORT,
            service_name=self.config.DB_SERVICE,
        )
        connection = oracledb.connect(
            user=self.config.DB_USER,
            password=self.config.DB_PASSWORD,
            dsn=dsn,
            tcp_connect_timeout=self.config.DB_CONNECT_TIMEOUT,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("ALTER SESSION SET CURRENT_SCHEMA = APPS")
            return connection
        except Exception:
            connection.close()
            raise

    def test_connection(self):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') FROM dual")
                schema = cursor.fetchone()[0]
            return {"success": schema == "APPS", "schema": schema}
        finally:
            connection.close()

    def get_tables(self):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM all_tables
                    WHERE owner = :owner AND temporary = 'N'
                    ORDER BY table_name
                    """,
                    owner=self.config.DB_SCHEMA,
                )
                return [{"table_name": row[0]} for row in cursor.fetchall()]
        finally:
            connection.close()

    def search_tables(self, query="", page=1, page_size=50):
        """Return one bounded, APPS-only page of case-insensitive table matches."""
        try:
            page = int(page)
            page_size = int(page_size)
        except (TypeError, ValueError) as error:
            raise ValueError("Invalid table search pagination.") from error
        if page < 1 or page_size < 1 or page_size > 50:
            raise ValueError("Table search page must be 1 or greater and page size at most 50.")

        query = str(query or "").strip().upper()
        if len(query) > 128:
            raise ValueError("Table search text is too long.")

        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_name
                    FROM all_tables
                    WHERE owner = :owner
                      AND temporary = 'N'
                      AND (:query IS NULL OR INSTR(UPPER(table_name), :query) > 0)
                    ORDER BY table_name
                    OFFSET :offset ROWS FETCH NEXT :fetch_count ROWS ONLY
                    """,
                    owner=self.config.DB_SCHEMA,
                    query=query or None,
                    offset=(page - 1) * page_size,
                    fetch_count=page_size + 1,
                )
                rows = cursor.fetchall()
            return {
                "tables": [
                    {"table_name": row[0]}
                    for row in rows[:page_size]
                ],
                "page": page,
                "page_size": page_size,
                "has_more": len(rows) > page_size,
            }
        finally:
            connection.close()

    def get_table_metadata(self, table_name):
        table_name = self._identifier(table_name, "table name")
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name, data_type, nullable, data_length,
                           data_precision, data_scale, data_default
                    FROM all_tab_columns
                    WHERE owner = :owner AND table_name = :table_name
                    ORDER BY column_id
                    """,
                    owner=self.config.DB_SCHEMA,
                    table_name=table_name,
                )
                column_rows = cursor.fetchall()
                if not column_rows:
                    raise ValueError(
                        f"Table {self.config.DB_SCHEMA}.{table_name} was not found "
                        "or is not accessible."
                    )
                cursor.execute(
                    """
                    SELECT column_name
                    FROM all_tab_identity_cols
                    WHERE owner = :owner AND table_name = :table_name
                    """,
                    owner=self.config.DB_SCHEMA,
                    table_name=table_name,
                )
                identity_columns = {row[0] for row in cursor.fetchall()}
                columns = [
                    {
                        "column_name": row[0],
                        "data_type": row[1],
                        "nullable": row[2],
                        "data_length": row[3],
                        "data_precision": row[4],
                        "data_scale": row[5],
                        "has_default": row[6] is not None,
                        "is_identity": row[0] in identity_columns,
                    }
                    for row in column_rows
                ]
                cursor.execute(
                    """
                    SELECT cols.column_name
                    FROM all_constraints cons
                    JOIN all_cons_columns cols
                      ON cols.owner = cons.owner
                     AND cols.constraint_name = cons.constraint_name
                     AND cols.table_name = cons.table_name
                    WHERE cons.owner = :owner
                      AND cons.table_name = :table_name
                      AND cons.constraint_type = 'P'
                    ORDER BY cols.position
                    """,
                    owner=self.config.DB_SCHEMA,
                    table_name=table_name,
                )
                primary_keys = [row[0] for row in cursor.fetchall()]
            return {
                "table_name": table_name,
                "schema": self.config.DB_SCHEMA,
                "columns": columns,
                "primary_keys": primary_keys,
                "primary_key": primary_keys[0] if len(primary_keys) == 1 else None,
            }
        finally:
            connection.close()

    def _fetch_blob_states(
        self,
        cursor,
        table_name,
        primary_keys,
        key_rows,
        blob_columns,
        for_update=False,
    ):
        table_name = self._identifier(table_name, "table name")
        primary_keys = [
            self._identifier(name, "primary key column")
            for name in primary_keys
        ]
        blob_columns = [
            self._identifier(name, "BLOB column")
            for name in blob_columns
        ]
        states = {row_number: None for row_number, _ in key_rows}
        if not key_rows:
            return states

        row_by_key = {
            tuple(values): row_number
            for row_number, values in key_rows
        }
        selected_columns = [
            f'"{name}"'
            for name in primary_keys
        ] + [
            f'CASE WHEN "{name}" IS NULL THEN 1 ELSE 0 END'
            for name in blob_columns
        ]
        table = f'"{self.config.DB_SCHEMA}"."{table_name}"'

        for start in range(0, len(key_rows), 100):
            batch = key_rows[start:start + 100]
            conditions = []
            bind_values = {}
            for key_index, (_, values) in enumerate(batch):
                parts = []
                for column_index, column in enumerate(primary_keys):
                    bind_name = f"k{key_index}_{column_index}"
                    parts.append(f'"{column}" = :{bind_name}')
                    bind_values[bind_name] = values[column_index]
                conditions.append("(" + " AND ".join(parts) + ")")
            statement = (
                f"SELECT {', '.join(selected_columns)} FROM {table} "
                f"WHERE {' OR '.join(conditions)}"
            )
            if for_update:
                statement += " FOR UPDATE"
            cursor.execute(statement, bind_values)
            for result in cursor.fetchall():
                key = tuple(result[:len(primary_keys)])
                row_number = row_by_key.get(key)
                if row_number is not None:
                    states[row_number] = {
                        column: bool(result[len(primary_keys) + index])
                        for index, column in enumerate(blob_columns)
                    }
        return states

    def get_blob_states(self, table_name, primary_keys, key_rows, blob_columns):
        """Read BLOB null-state by mapped primary key for the import review."""
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                return self._fetch_blob_states(
                    cursor,
                    table_name,
                    primary_keys,
                    key_rows,
                    blob_columns,
                )
        finally:
            connection.close()

    @staticmethod
    def _bind_type(data_type):
        data_type = data_type.upper()
        if data_type == "BLOB":
            return oracledb.DB_TYPE_BLOB
        if data_type in {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}:
            return oracledb.DB_TYPE_NUMBER
        if data_type == "DATE":
            return oracledb.DB_TYPE_DATE
        if data_type.startswith("TIMESTAMP"):
            return oracledb.DB_TYPE_TIMESTAMP
        if data_type == "CLOB":
            return oracledb.DB_TYPE_CLOB
        if data_type == "NCLOB":
            return oracledb.DB_TYPE_NCLOB
        if data_type in {"NCHAR", "NVARCHAR2"}:
            return oracledb.DB_TYPE_NVARCHAR
        if data_type in {"CHAR", "VARCHAR2"}:
            return oracledb.DB_TYPE_VARCHAR
        return None

    def merge_blob_records(
        self,
        table_name,
        primary_keys,
        key_rows,
        blob_mappings,
        column_types,
        expected_states,
        rows,
    ):
        """Insert new rows or update only explicitly mapped columns."""
        table_name = self._identifier(table_name, "table name")
        primary_keys = [
            self._identifier(name, "primary key column")
            for name in primary_keys
        ]
        blob_columns = [
            self._identifier(item["oracle_column"], "BLOB column")
            for item in blob_mappings
        ]
        normalized_types = {
            self._identifier(name, "column name"): data_type.upper()
            for name, data_type in column_types.items()
        }
        expected = {
            int(row_number): state
            for row_number, state in expected_states.items()
        }
        key_values_by_row = dict(key_rows)
        errors = []
        statuses = {}
        inserted_rows = 0
        updated_files = 0
        replaced_files = 0
        skipped_files = 0

        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                current_states = self._fetch_blob_states(
                    cursor,
                    table_name,
                    primary_keys,
                    key_rows,
                    blob_columns,
                    for_update=True,
                )
                if current_states != expected:
                    raise RuntimeError(
                        "Oracle BLOB values changed after review. Review the "
                        "import again before uploading."
                    )

                pending = []
                pending_bytes = 0

                def flush_batch():
                    nonlocal inserted_rows
                    nonlocal updated_files
                    nonlocal replaced_files
                    nonlocal skipped_files
                    nonlocal pending_bytes
                    if not pending:
                        return
                    grouped = {}
                    for operation in pending:
                        grouped.setdefault(operation["statement"], []).append(
                            operation
                        )
                    for statement, operations in grouped.items():
                        bind_columns = (
                            operations[0]["bind_columns"]
                            + primary_keys
                            if operations[0]["existing"]
                            else operations[0]["bind_columns"]
                        )
                        cursor.setinputsizes(*[
                            self._bind_type(normalized_types[column])
                            for column in bind_columns
                        ])
                        cursor.executemany(
                            statement,
                            [operation["values"] for operation in operations],
                            batcherrors=True,
                        )
                        failed_offsets = {
                            error.offset: str(error)
                            for error in cursor.getbatcherrors()
                        }
                        for offset, operation in enumerate(operations):
                            row_number = operation["row"]
                            if offset in failed_offsets:
                                errors.append({
                                    "row": row_number,
                                    "error": failed_offsets[offset],
                                })
                                statuses[row_number] = {
                                    column: "FAILED"
                                    for column in blob_columns
                                }
                                continue
                            if not operation["existing"]:
                                inserted_rows += 1
                            updated_files += operation["status_counts"]["UPDATE"]
                            replaced_files += operation["status_counts"]["REPLACE"]
                            skipped_files += operation["status_counts"]["SKIP"]
                            statuses[row_number] = operation["statuses"]
                    pending.clear()
                    pending_bytes = 0

                for row_number, data_values, blob_values in rows:
                    existing_state = current_states[row_number]
                    row_statuses = {}
                    status_counts = {"UPDATE": 0, "REPLACE": 0, "SKIP": 0}
                    values = dict(data_values)
                    statement = None
                    bind_columns = []
                    is_existing = existing_state is not None

                    for mapping in blob_mappings:
                        blob_column = mapping["oracle_column"].upper()
                        blob_value = blob_values[blob_column]
                        if not is_existing:
                            status = "INSERT"
                            values[blob_column] = blob_value["content"]
                            values.update(blob_value["metadata"])
                        elif existing_state[blob_column]:
                            status = "UPDATE"
                            values[blob_column] = blob_value["content"]
                            values.update(blob_value["metadata"])
                            status_counts["UPDATE"] += 1
                        else:
                            status = blob_value["action"]
                            if status == "REPLACE":
                                values[blob_column] = blob_value["content"]
                                values.update(blob_value["metadata"])
                                status_counts["REPLACE"] += 1
                            else:
                                status = "SKIP"
                                status_counts["SKIP"] += 1
                        row_statuses[blob_column] = status

                    if not is_existing:
                        bind_columns = list(values)
                        quoted_columns = ", ".join(
                            f'"{column}"' for column in bind_columns
                        )
                        binds = ", ".join(
                            f":{index}"
                            for index in range(1, len(bind_columns) + 1)
                        )
                        statement = (
                            f'INSERT INTO "{self.config.DB_SCHEMA}"."{table_name}" '
                            f"({quoted_columns}) VALUES ({binds})"
                        )
                        operation_values = tuple(
                            values[column] for column in bind_columns
                        )
                    else:
                        update_columns = [
                            column for column in values
                            if column not in primary_keys
                        ]
                        if not update_columns:
                            skipped_files += len(blob_columns)
                            statuses[row_number] = row_statuses
                            continue
                        bind_columns = update_columns
                        set_clause = ", ".join(
                            f'"{column}" = :{index}'
                            for index, column in enumerate(bind_columns, start=1)
                        )
                        where_clause = " AND ".join(
                            f'"{column}" = :{len(bind_columns) + index}'
                            for index, column in enumerate(
                                primary_keys,
                                start=1,
                            )
                        )
                        statement = (
                            f'UPDATE "{self.config.DB_SCHEMA}"."{table_name}" '
                            f"SET {set_clause} WHERE {where_clause}"
                        )
                        primary_values = key_values_by_row[row_number]
                        operation_values = tuple(
                            values[column] for column in bind_columns
                        ) + tuple(primary_values)

                    payload_bytes = sum(
                        len(value)
                        for value in operation_values
                        if isinstance(value, bytes)
                    )
                    batch_size = self.config.IMPORT_BATCH_SIZE
                    batch_bytes = self.config.MAX_BLOB_BATCH_BYTES
                    if pending and (
                        len(pending) >= batch_size
                        or pending_bytes + payload_bytes > batch_bytes
                    ):
                        flush_batch()
                    pending.append({
                        "row": row_number,
                        "statement": statement,
                        "values": operation_values,
                        "bind_columns": bind_columns,
                        "existing": is_existing,
                        "statuses": row_statuses,
                        "status_counts": status_counts,
                    })
                    pending_bytes += payload_bytes
                flush_batch()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        return {
            "errors": errors,
            "statuses": statuses,
            "inserted_rows": inserted_rows,
            "updated_files": updated_files,
            "replaced_files": replaced_files,
            "skipped_files": skipped_files,
        }

    def insert_many(
        self,
        table_name,
        column_names,
        rows,
        blob_columns=None,
        column_types=None,
    ):
        """Stream rows through bounded array-DML batches and return row errors."""
        table_name = self._identifier(table_name, "table name")
        columns = [self._identifier(name, "column name") for name in column_names]
        if not columns:
            raise ValueError("At least one Oracle column must be mapped.")
        if len(columns) != len(set(columns)):
            raise ValueError("Oracle columns may only be mapped once.")
        blob_columns = {name.upper() for name in (blob_columns or [])}
        column_types = {
            name.upper(): data_type.upper()
            for name, data_type in (column_types or {}).items()
        }

        quoted_columns = ", ".join(f'"{name}"' for name in columns)
        binds = ", ".join(f":{index}" for index in range(1, len(columns) + 1))
        statement = (
            f'INSERT INTO "{self.config.DB_SCHEMA}"."{table_name}" '
            f"({quoted_columns}) VALUES ({binds})"
        )
        errors = []
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                bind_types = []
                for name in columns:
                    data_type = column_types.get(name, "")
                    if name in blob_columns or data_type == "BLOB":
                        bind_types.append(oracledb.DB_TYPE_BLOB)
                    elif data_type in {
                        "NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"
                    }:
                        bind_types.append(oracledb.DB_TYPE_NUMBER)
                    elif data_type == "DATE":
                        bind_types.append(oracledb.DB_TYPE_DATE)
                    elif data_type.startswith("TIMESTAMP"):
                        bind_types.append(oracledb.DB_TYPE_TIMESTAMP)
                    elif data_type == "CLOB":
                        bind_types.append(oracledb.DB_TYPE_CLOB)
                    elif data_type == "NCLOB":
                        bind_types.append(oracledb.DB_TYPE_NCLOB)
                    else:
                        bind_types.append(None)
                if any(bind_type is not None for bind_type in bind_types):
                    cursor.setinputsizes(*bind_types)
                rows_iterator = iter(rows)
                pending_row = None
                while True:
                    batch = []
                    batch_bytes = 0
                    while len(batch) < self.config.IMPORT_BATCH_SIZE:
                        if pending_row is None:
                            next_row = next(rows_iterator, None)
                        else:
                            next_row = pending_row
                            pending_row = None
                        if next_row is None:
                            break
                        payload_bytes = sum(
                            len(value)
                            for value in next_row[1]
                            if isinstance(value, bytes)
                        )
                        if (
                            batch
                            and batch_bytes + payload_bytes
                            > self.config.MAX_BLOB_BATCH_BYTES
                        ):
                            pending_row = next_row
                            break
                        batch.append(next_row)
                        batch_bytes += payload_bytes
                    if not batch:
                        break
                    cursor.executemany(
                        statement,
                        [values for _, values in batch],
                        batcherrors=True,
                    )
                    for error in cursor.getbatcherrors():
                        source_row = batch[error.offset][0]
                        errors.append({"row": source_row, "error": str(error)})
            connection.commit()
            return errors
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
