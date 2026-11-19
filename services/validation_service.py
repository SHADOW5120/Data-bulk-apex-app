
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from numbers import Number

from openpyxl.utils.datetime import WINDOWS_EPOCH, from_excel

from services.file_service import (
    find_zip_file,
    validate_file_type
)


NUMBER_TYPES = {
    "NUMBER",
    "FLOAT",
    "BINARY_FLOAT",
    "BINARY_DOUBLE"
}

DATE_TYPES = {"DATE", "TIMESTAMP"}


def validate_value(value, oracle_type):
    if value is None or value == "":
        return None

    oracle_type = oracle_type.upper()

    if oracle_type in NUMBER_TYPES:
        try:
            return Decimal(str(value))
        except InvalidOperation as error:
            raise ValueError(f"Invalid NUMBER value: {value}") from error

    if oracle_type in DATE_TYPES or oracle_type.startswith("TIMESTAMP"):
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if isinstance(value, Number) and not isinstance(value, bool):
            try:
                converted = from_excel(float(value))
            except (OverflowError, TypeError, ValueError) as error:
                raise ValueError(
                    f"Invalid Excel date serial: {value}"
                ) from error
            if isinstance(converted, datetime):
                return converted
            if isinstance(converted, time):
                return datetime.combine(WINDOWS_EPOCH.date(), converted)
            raise ValueError(f"Invalid Excel date serial: {value}")
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(str(value))
                return datetime.combine(parsed_date, datetime.min.time())
            except ValueError as error:
                raise ValueError(f"Invalid DATE value: {value}") from error

    return value if isinstance(value, (str, bytes)) else str(value)


def get_primary_key_rows(records, metadata, mappings):
    primary_keys = metadata.get("primary_keys") or (
        [metadata["primary_key"]]
        if metadata.get("primary_key")
        else []
    )
    if not primary_keys:
        raise ValueError(
            "The selected Oracle table has no primary key; BLOB updates "
            "cannot safely identify existing records."
        )

    oracle_columns = {
        column["column_name"].upper(): column
        for column in metadata["columns"]
    }
    data_mappings = {
        item["oracle_column"].upper(): item
        for item in mappings
        if item["column_type"] == "DATA"
    }
    missing_keys = [
        key for key in primary_keys
        if key.upper() not in data_mappings
    ]
    if missing_keys:
        raise ValueError(
            "Map every Oracle primary-key column to an Excel column before "
            "using ZIP BLOB mapping: " + ", ".join(missing_keys)
        )

    key_rows = []
    seen_keys = {}
    for row_number, record in enumerate(records, start=2):
        values = []
        for key in primary_keys:
            key = key.upper()
            mapping = data_mappings[key]
            value = record.get(mapping["excel_column"])
            if value is None or value == "":
                raise ValueError(
                    f"Excel row {row_number} has an empty primary-key value "
                    f"for {key}."
                )
            values.append(
                validate_value(value, oracle_columns[key]["data_type"])
            )
        key_tuple = tuple(values)
        if key_tuple in seen_keys:
            raise ValueError(
                f"Excel rows {seen_keys[key_tuple]} and {row_number} have "
                "duplicate Oracle primary-key values."
            )
        seen_keys[key_tuple] = row_number
        key_rows.append((row_number, key_tuple))
    return primary_keys, key_rows


def validate_file_metadata(zip_review, mappings, metadata):
    oracle_columns = {
        column["column_name"].upper(): column
        for column in metadata["columns"]
    }
    blob_mappings = {
        mapping["oracle_column"].upper(): mapping
        for mapping in mappings
        if mapping["column_type"] in ("IMAGE", "FILE")
    }
    errors = []
    mime_pattern = re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/"
        r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
    )

    for folder in zip_review["folders"]:
        mapping = blob_mappings.get(folder["oracle_column"].upper())
        if mapping is None:
            continue
        for assignment in folder["assignments"]:
            file_metadata = assignment["metadata"]
            row_number = assignment["row"]
            mime_type = file_metadata["mime_type"]
            if (
                not file_metadata["filename"]
                or not file_metadata["extension"]
                or not mime_pattern.fullmatch(mime_type)
                or file_metadata["size"] < 0
            ):
                errors.append({
                    "row": row_number,
                    "column": mapping["oracle_column"],
                    "error": "ZIP file metadata is invalid.",
                })
                continue

            for field, column_name in mapping.get(
                "metadata_columns", {}
            ).items():
                column = oracle_columns[column_name.upper()]
                value = file_metadata[field]
                if column["data_type"].upper() == "VARCHAR2":
                    max_length = column.get("data_length")
                    if (
                        max_length is not None
                        and len(str(value).encode("utf-8")) > max_length
                    ):
                        errors.append({
                            "row": row_number,
                            "column": column_name,
                            "error": (
                                f"{field} metadata exceeds the Oracle column "
                                f"length ({max_length} bytes)."
                            ),
                        })
                elif column["data_type"].upper() == "NUMBER":
                    precision = column.get("data_precision")
                    scale = column.get("data_scale") or 0
                    max_integer_value = (
                        int(Decimal(10 ** precision - 1).scaleb(-scale))
                        if precision is not None
                        else None
                    )
                    if (
                        max_integer_value is not None
                        and value > max_integer_value
                    ):
                        errors.append({
                            "row": row_number,
                            "column": column_name,
                            "error": (
                                f"File size does not fit Oracle NUMBER "
                                f"precision {precision}, scale {scale}."
                            ),
                        })
    return errors


def validate_records(
    records,
    metadata,
    mappings,
    file_index,
    row_operations=None,
):

    oracle_columns = {
        col["column_name"].upper(): col
        for col in metadata["columns"]
    }

    errors = []

    mapped_oracle_columns = {
        item["oracle_column"].upper()
        for item in mappings
    }
    mapped_oracle_columns.update(
        column.upper()
        for item in mappings
        for column in item.get("metadata_columns", {}).values()
    )

    for row_number, record in enumerate(
        records,
        start=2
    ):
        operation = (row_operations or {}).get(row_number, "INSERT")

        if operation not in {"INSERT", "UPDATE", "UNVERIFIED"}:
            raise ValueError(
                f"Invalid operation for Excel row {row_number}: {operation}"
            )

        if operation == "INSERT":
            for column in metadata["columns"]:
                column_name = column["column_name"].upper()
                if (
                    column_name not in mapped_oracle_columns
                    and column.get("nullable", "Y") == "N"
                    and not column.get("has_default", False)
                    and not column.get("is_identity", False)
                ):
                    errors.append({
                        "row": row_number,
                        "operation": "INSERT",
                        "column": column_name,
                        "error": (
                            "Required Oracle column is not mapped and has "
                            "no default for INSERT."
                        ),
                    })

        for mapping in mappings:

            excel_column = mapping["excel_column"]

            oracle_column = mapping["oracle_column"]

            column_type = mapping["column_type"]

            oracle_info = oracle_columns[oracle_column]

            if column_type in ("IMAGE", "FILE") and "zip_folder" in mapping:
                continue

            value = record.get(excel_column)

            if value is None or value == "":

                if oracle_info.get("nullable", "Y") == "N":

                    errors.append({
                        "row": row_number,
                        "operation": operation,
                        "column": excel_column,
                        "error": "Required value is empty."
                    })

                continue

            try:

                if column_type == "DATA":

                    validate_value(
                        value,
                        oracle_info["data_type"]
                    )

                else:

                    internal_path = str(value).strip()

                    archive_name = find_zip_file(
                        file_index,
                        internal_path
                    )

                    if archive_name is None:

                        raise ValueError(
                            f"File not found: {internal_path}"
                        )

                    if not validate_file_type(
                        internal_path,
                        column_type
                    ):

                        raise ValueError(
                            f"Invalid {column_type} file: {internal_path}"
                        )

            except Exception as error:

                errors.append({
                    "row": row_number,
                    "operation": operation,
                    "column": excel_column,
                    "error": str(error)
                })

    return errors
