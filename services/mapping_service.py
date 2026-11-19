
from collections.abc import Mapping

from services.file_service import normalize_path


_FILE_METADATA_TYPES = {
    "filename": "VARCHAR2",
    "extension": "VARCHAR2",
    "size": "NUMBER",
    "mime_type": "VARCHAR2",
}


def build_mapping(
    excel_columns,
    oracle_metadata,
    selected_mappings
):
    if not isinstance(excel_columns, (list, tuple, set)):
        raise ValueError("Excel columns must be a list of column names.")
    if not isinstance(oracle_metadata, Mapping):
        raise ValueError("Oracle metadata is invalid.")
    metadata_columns = oracle_metadata.get("columns")
    if not isinstance(metadata_columns, (list, tuple)):
        raise ValueError("Oracle metadata does not contain a valid column list.")
    if not isinstance(selected_mappings, (list, tuple)):
        raise ValueError("Selected mappings must be a list.")

    available_excel_columns = set()
    for column in excel_columns:
        if not isinstance(column, str) or not column.strip():
            raise ValueError("Excel column names must be non-empty text.")
        available_excel_columns.add(column.strip())

    oracle_columns = {}
    for column in metadata_columns:
        if not isinstance(column, Mapping):
            raise ValueError("Oracle column metadata is invalid.")
        name = column.get("column_name")
        data_type = column.get("data_type")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Oracle column metadata has an invalid column name.")
        if not isinstance(data_type, str) or not data_type.strip():
            raise ValueError(f"Oracle column {name} has an invalid data type.")
        oracle_columns[name.strip().upper()] = column

    result = []
    used_oracle_columns = set()

    for mapping in selected_mappings:
        if not isinstance(mapping, Mapping):
            raise ValueError("Each selected mapping must be an object.")
        excel_value = mapping.get("excel_column", "")
        oracle_value = mapping.get("oracle_column")
        type_value = mapping.get("column_type")
        if not isinstance(excel_value, str):
            raise ValueError("Excel column names must be text.")
        if not isinstance(oracle_value, str) or not oracle_value.strip():
            raise ValueError("An Oracle column must be selected for each mapping.")
        if not isinstance(type_value, str) or not type_value.strip():
            raise ValueError("A mapping type must be selected for each mapping.")

        excel_column = excel_value.strip()
        oracle_column = oracle_value.strip().upper()
        column_type = type_value.strip().upper()

        if oracle_column not in oracle_columns:
            raise ValueError(f"Oracle column not found: {oracle_column}")
        if oracle_column in used_oracle_columns:
            raise ValueError(f"Oracle column mapped more than once: {oracle_column}")
        if column_type not in ("DATA", "IMAGE", "FILE"):
            raise ValueError(f"Invalid mapping type: {column_type}")

        oracle_info = oracle_columns[oracle_column]
        oracle_type = oracle_info["data_type"].upper()

        if column_type == "DATA" and oracle_type == "BLOB":
            raise ValueError(f"{oracle_column} is BLOB. Use FILE or IMAGE.")
        if column_type in ("IMAGE", "FILE") and oracle_type != "BLOB":
            raise ValueError(f"{oracle_column} must be BLOB.")

        item = {
            "excel_column": excel_column,
            "oracle_column": oracle_column,
            "column_type": column_type,
            "nullable": oracle_info.get("nullable", "Y")
        }
        if column_type == "DATA":
            if not excel_column:
                raise ValueError("An Excel column must be selected for each DATA mapping.")
            if excel_column not in available_excel_columns:
                raise ValueError(f"Excel column not found: {excel_column}")
        else:
            folder = mapping.get("zip_folder")
            if not isinstance(folder, str) or not folder.strip():
                raise ValueError(
                    f"Select a ZIP folder for Oracle column {oracle_column}."
                )
            normalized_folder = normalize_path(folder.strip())
            if normalized_folder == ".":
                raise ValueError("A ZIP folder must be selected for each BLOB mapping.")
            item["excel_column"] = ""
            item["zip_folder"] = normalized_folder
            metadata_columns = mapping.get("metadata_columns", {})
            if not isinstance(metadata_columns, Mapping):
                raise ValueError(
                    f"Metadata column mappings for {oracle_column} are invalid."
                )
            normalized_metadata_columns = {}
            for field, metadata_column in metadata_columns.items():
                if field not in _FILE_METADATA_TYPES:
                    raise ValueError(f"Invalid file metadata field: {field}")
                if metadata_column in (None, ""):
                    continue
                if not isinstance(metadata_column, str):
                    raise ValueError(
                        f"Oracle column for {field} must be text."
                    )
                metadata_column = metadata_column.strip().upper()
                if metadata_column not in oracle_columns:
                    raise ValueError(
                        f"Oracle metadata column not found: {metadata_column}"
                    )
                if metadata_column in used_oracle_columns:
                    raise ValueError(
                        f"Oracle column mapped more than once: {metadata_column}"
                    )
                metadata_type = oracle_columns[metadata_column]["data_type"].upper()
                expected_type = _FILE_METADATA_TYPES[field]
                if metadata_type != expected_type:
                    raise ValueError(
                        f"{metadata_column} must be {expected_type} for file {field}."
                    )
                normalized_metadata_columns[field] = metadata_column
                used_oracle_columns.add(metadata_column)
            item["metadata_columns"] = normalized_metadata_columns

        result.append(item)
        used_oracle_columns.add(oracle_column)

    return result


def get_data_mappings(mappings):

    return [
        item for item in mappings
        if item["column_type"] == "DATA"
    ]


def get_blob_mappings(mappings):

    return [
        item for item in mappings
        if item["column_type"] in ("IMAGE", "FILE")
    ]
