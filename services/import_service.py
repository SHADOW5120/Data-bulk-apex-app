
import zipfile

from services.file_service import (
    find_zip_file,
    read_zip_member
)

from services.mapping_service import (
    get_data_mappings,
    get_blob_mappings
)

from services.validation_service import (
    validate_value
)


def import_records(
    records,
    zip_path,
    file_index,
    table_name,
    metadata,
    mappings,
    oracle_service,
    assignment_map=None,
    assignment_metadata=None,
    primary_keys=None,
    key_rows=None,
    expected_blob_states=None,
    blob_actions=None,
):
    oracle_columns = {
        column["column_name"].upper(): column
        for column in metadata["columns"]
    }
    data_mappings = get_data_mappings(mappings)
    blob_mappings = get_blob_mappings(mappings)
    mapped_columns = [item["oracle_column"] for item in mappings]

    file_counts = {}
    errors = []
    failed_files = 0
    prepared_row_count = 0

    def prepared_rows(archive=None):
        nonlocal failed_files, prepared_row_count
        for row_number, record in enumerate(records, start=2):
            attachment_count = sum(
                1
                for mapping in blob_mappings
                if (
                    "zip_folder" in mapping
                    or record.get(mapping["excel_column"]) not in (None, "")
                )
            )
            try:
                values = {}
                for mapping in data_mappings:
                    oracle_column = mapping["oracle_column"]
                    values[oracle_column] = validate_value(
                        record.get(mapping["excel_column"]),
                        oracle_columns[oracle_column]["data_type"],
                    )

                for mapping in blob_mappings:
                    oracle_column = mapping["oracle_column"]
                    if "zip_folder" in mapping:
                        archive_name = (assignment_map or {}).get(
                            oracle_column, {}
                        ).get(row_number)
                    else:
                        file_path = record.get(mapping["excel_column"])
                        if file_path is None or str(file_path).strip() == "":
                            values[oracle_column] = None
                            continue
                        archive_name = find_zip_file(file_index, file_path)
                    if archive_name is None:
                        raise FileNotFoundError(
                            f"File assignment not found for row {row_number} "
                            f"and Oracle column {oracle_column}."
                        )
                    values[oracle_column] = read_zip_member(
                        archive,
                        archive_name,
                    )

                prepared_row_count += 1
                file_counts[row_number] = attachment_count
                yield (
                    row_number,
                    tuple(values[column] for column in mapped_columns),
                )
            except Exception as error:
                failed_files += attachment_count
                errors.append({"row": row_number, "error": str(error)})

    blob_columns = [item["oracle_column"] for item in blob_mappings]
    column_types = {
        item["column_name"].upper(): item["data_type"]
        for item in metadata["columns"]
    }
    folder_based_blobs = bool(blob_mappings) and all(
        "zip_folder" in item
        for item in blob_mappings
    )
    if folder_based_blobs:
        if not primary_keys or key_rows is None or expected_blob_states is None:
            raise ValueError(
                "Primary-key state is required for safe BLOB insert/update."
            )

        def prepared_folder_rows(archive):
            nonlocal failed_files, prepared_row_count
            for row_number, record in enumerate(records, start=2):
                try:
                    data_values = {}
                    for mapping in data_mappings:
                        oracle_column = mapping["oracle_column"]
                        data_values[oracle_column] = validate_value(
                            record.get(mapping["excel_column"]),
                            oracle_columns[oracle_column]["data_type"],
                        )

                    row_blob_values = {}
                    row_state = expected_blob_states.get(
                        str(row_number),
                        expected_blob_states.get(row_number),
                    )
                    for mapping in blob_mappings:
                        oracle_column = mapping["oracle_column"]
                        archive_name = (assignment_map or {}).get(
                            oracle_column, {}
                        ).get(row_number)
                        file_metadata = (assignment_metadata or {}).get(
                            oracle_column, {}
                        ).get(row_number)
                        if archive_name is None or file_metadata is None:
                            raise FileNotFoundError(
                                f"ZIP file assignment is missing for Excel row "
                                f"{row_number} and Oracle column {oracle_column}."
                            )

                        action = (blob_actions or {}).get(
                            row_number, {}
                        ).get(oracle_column, "SKIP")
                        if action not in {"REPLACE", "SKIP"}:
                            raise ValueError(
                                f"Invalid BLOB action for row {row_number}: {action}"
                            )
                        existing_non_null = (
                            isinstance(row_state, dict)
                            and not row_state.get(oracle_column, True)
                        )
                        blob_value = {
                            "action": action,
                            "metadata": {
                                metadata_column: file_metadata[field]
                                for field, metadata_column
                                in mapping.get("metadata_columns", {}).items()
                            },
                        }
                        if not existing_non_null or action == "REPLACE":
                            blob_value["content"] = read_zip_member(
                                archive,
                                archive_name,
                            )
                        row_blob_values[oracle_column] = blob_value

                    prepared_row_count += 1
                    file_counts[row_number] = len(blob_mappings)
                    yield row_number, data_values, row_blob_values
                except Exception as error:
                    failed_files += len(blob_mappings)
                    errors.append({"row": row_number, "error": str(error)})

        with zipfile.ZipFile(zip_path, "r") as archive:
            merge_result = oracle_service.merge_blob_records(
                table_name=table_name,
                primary_keys=primary_keys,
                key_rows=key_rows,
                blob_mappings=blob_mappings,
                column_types=column_types,
                expected_states=expected_blob_states,
                rows=prepared_folder_rows(archive),
            )
        errors.extend(merge_result["errors"])
        failed_database_rows = {
            item["row"] for item in merge_result["errors"]
        }
        success_rows = prepared_row_count - len(failed_database_rows)
        success_files = sum(
            count
            for row_number, count in file_counts.items()
            if row_number not in failed_database_rows
        )
        failed_files += sum(
            file_counts.get(row_number, 0)
            for row_number in failed_database_rows
        )
        failed_rows = len(errors)
        return {
            "total_rows": len(records),
            "success_rows": success_rows,
            "failed_rows": failed_rows,
            "success_files": success_files,
            "failed_files": failed_files,
            "inserted_rows": merge_result["inserted_rows"],
            "updated_files": merge_result["updated_files"],
            "replaced_files": merge_result["replaced_files"],
            "skipped_files": merge_result["skipped_files"],
            "errors": errors,
            "status": (
                "SUCCESS" if failed_rows == 0
                else "PARTIAL" if success_rows > 0
                else "FAILED"
            ),
        }

    if blob_mappings:
        with zipfile.ZipFile(zip_path, "r") as archive:
            database_errors = oracle_service.insert_many(
                table_name,
                mapped_columns,
                prepared_rows(archive),
                blob_columns=blob_columns,
                column_types=column_types,
            )
    else:
        database_errors = oracle_service.insert_many(
            table_name,
            mapped_columns,
            prepared_rows(),
            blob_columns=blob_columns,
            column_types=column_types,
        )
    errors.extend(database_errors)
    failed_database_rows = {item["row"] for item in database_errors}
    success_rows = prepared_row_count - len(database_errors)
    success_files = sum(
        count
        for row_number, count in file_counts.items()
        if row_number not in failed_database_rows
    )
    failed_files += sum(
        file_counts.get(item["row"], 0)
        for item in database_errors
    )
    failed_rows = len(errors)

    return {
        "total_rows": len(records),
        "success_rows": success_rows,
        "failed_rows": failed_rows,
        "success_files": success_files,
        "failed_files": failed_files,
        "errors": errors,
        "status": (
            "SUCCESS" if failed_rows == 0
            else "PARTIAL" if success_rows > 0
            else "FAILED"
        ),
    }
