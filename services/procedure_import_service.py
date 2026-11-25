import zipfile

from services.file_service import read_zip_member
from services.validation_service import validate_value


_FILE_METADATA_FIELDS = {
    "FILE_NAME": "filename",
    "FILE_EXTENSION": "extension",
    "FILE_SIZE": "size",
    "FILE_MIME_TYPE": "mime_type",
}


def import_procedure_records(
    records,
    zip_path,
    mappings,
    procedure,
    oracle_service,
    assignment_map=None,
    assignment_metadata=None,
):
    parameter_by_name = {
        parameter["name"].upper(): parameter
        for parameter in procedure["parameters"]
    }
    blob_mappings = [
        mapping for mapping in mappings
        if mapping["source"] == "ZIP_BLOB"
    ]
    file_counts = {}
    errors = []

    def rows(archive=None):
        for row_number, record in enumerate(records, start=2):
            file_counts[row_number] = len(blob_mappings)
            try:
                values = {}
                for mapping in mappings:
                    name = mapping["parameter"]
                    parameter = parameter_by_name[name]
                    source = mapping["source"]
                    if source == "EXCEL":
                        value = record.get(mapping["excel_column"])
                        value = validate_value(value, parameter["data_type"])
                    elif source == "CONSTANT":
                        value = validate_value(
                            mapping["constant"],
                            parameter["data_type"],
                        )
                    elif source == "ZIP_BLOB":
                        archive_name = (assignment_map or {}).get(
                            name, {}
                        ).get(row_number)
                        if archive is None or archive_name is None:
                            raise FileNotFoundError(
                                f"ZIP file assignment is missing for Excel "
                                f"row {row_number} and parameter {name}."
                            )
                        value = read_zip_member(archive, archive_name)
                    else:
                        field = _FILE_METADATA_FIELDS[source]
                        blob_parameter = mapping["blob_parameter"]
                        file_metadata = (assignment_metadata or {}).get(
                            blob_parameter, {}
                        ).get(row_number)
                        if file_metadata is None:
                            raise FileNotFoundError(
                                f"ZIP file metadata is missing for Excel row "
                                f"{row_number} and parameter {name}."
                            )
                        value = file_metadata[field]
                        value = validate_value(value, parameter["data_type"])
                    values[name] = value

                yield row_number, values
            except Exception as error:
                errors.append({"row": row_number, "error": str(error)})

    if blob_mappings:
        if not zip_path:
            raise ValueError("A ZIP file is required for mapped BLOB parameters.")
        with zipfile.ZipFile(zip_path, "r") as archive:
            errors.extend(
                oracle_service.execute_procedure_rows(
                    procedure,
                    rows(archive),
                )
            )
    else:
        errors.extend(
            oracle_service.execute_procedure_rows(
                procedure,
                rows(),
            )
        )

    failed_rows = {
        item["row"] for item in errors if isinstance(item.get("row"), int)
    }
    failed_files = sum(
        file_counts.get(row_number, 0)
        for row_number in failed_rows
    )
    success_rows = len(records) - len(failed_rows)
    success_files = sum(
        count
        for row_number, count in file_counts.items()
        if row_number not in failed_rows
    )
    return {
        "total_rows": len(records),
        "success_rows": success_rows,
        "failed_rows": len(failed_rows),
        "success_files": success_files,
        "failed_files": failed_files,
        "errors": errors,
        "status": (
            "SUCCESS" if not failed_rows
            else "PARTIAL" if success_rows
            else "FAILED"
        ),
    }
