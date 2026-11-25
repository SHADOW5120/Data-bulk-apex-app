from collections.abc import Mapping

from services.file_service import normalize_path
from services.validation_service import validate_value


_NUMBER_TYPES = {
    "NUMBER",
    "FLOAT",
    "BINARY_FLOAT",
    "BINARY_DOUBLE",
    "INTEGER",
    "PLS_INTEGER",
    "BINARY_INTEGER",
    "SMALLINT",
}
_STRING_TYPES = {
    "CHAR",
    "VARCHAR",
    "VARCHAR2",
    "NCHAR",
    "NVARCHAR2",
    "CLOB",
    "NCLOB",
}
_DATE_TYPES = {"DATE"}
_FILE_SOURCES = {
    "FILE_NAME": ("filename", _STRING_TYPES),
    "FILE_EXTENSION": ("extension", _STRING_TYPES),
    "FILE_SIZE": ("size", _NUMBER_TYPES),
    "FILE_MIME_TYPE": ("mime_type", _STRING_TYPES),
}
_SUPPORTED_TYPES = _NUMBER_TYPES | _STRING_TYPES | _DATE_TYPES | {"BLOB"}


def _validate_parameter_type(parameter):
    data_type = str(parameter.get("data_type") or "").upper()
    if parameter.get("data_level", 0) or parameter.get("type_owner") or (
        data_type not in _SUPPORTED_TYPES
        and not data_type.startswith("TIMESTAMP")
    ):
        raise ValueError(
            f"Unsupported Oracle parameter type for {parameter.get('name')}: "
            f"{data_type or 'unknown'}"
        )
    return data_type


def validate_procedure_mappings(parameters, selected_mappings, excel_columns):
    if not isinstance(selected_mappings, (list, tuple)):
        raise ValueError("Procedure parameter mappings must be a list.")

    available_excel_columns = set(excel_columns)
    parameter_by_name = {}
    for parameter in parameters:
        name = parameter.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Oracle procedure contains an unnamed parameter.")
        name = name.strip().upper()
        if name in parameter_by_name:
            raise ValueError(f"Duplicate Oracle parameter name: {name}")
        parameter_by_name[name] = parameter
        _validate_parameter_type(parameter)

    result = []
    seen = set()
    blob_parameters = set()
    for mapping in selected_mappings:
        if not isinstance(mapping, Mapping):
            raise ValueError("Each procedure parameter mapping must be an object.")
        name = str(mapping.get("parameter") or "").strip().upper()
        parameter = parameter_by_name.get(name)
        if parameter is None:
            raise ValueError(f"Oracle procedure parameter not found: {name}")
        if parameter["mode"] == "OUT":
            raise ValueError(f"OUT parameter {name} cannot be mapped as input.")
        if name in seen:
            raise ValueError(f"Oracle parameter mapped more than once: {name}")
        seen.add(name)

        mapping_required = mapping.get("mapping_required", False)
        if not isinstance(mapping_required, bool):
            raise ValueError(
                f"Mapping requirement for parameter {name} is invalid."
            )
        source = str(mapping.get("source") or "").strip().upper()
        if not source:
            if mapping_required:
                raise ValueError(
                    f"Parameter {name} requires a mapping source. Choose an "
                    "Excel column, constant, or attachment source."
                )
            continue

        if source == "CONSTANT":
            constant = str(mapping.get("constant") or "")
            if not constant.strip():
                if mapping_required:
                    raise ValueError(
                        f"Required constant for parameter {name} is blank. "
                        "Provide a value or choose another source."
                    )
                continue

        data_type = str(parameter["data_type"]).upper()
        item = {
            "parameter": name,
            "source": source,
            "data_type": data_type,
            "mapping_required": mapping_required,
        }

        if source == "EXCEL":
            excel_column = mapping.get("excel_column")
            if excel_column not in available_excel_columns:
                raise ValueError(
                    f"Excel column not found for parameter {name}: {excel_column}"
                )
            item["excel_column"] = excel_column
        elif source == "CONSTANT":
            item["constant"] = constant
        elif source == "ZIP_BLOB":
            if data_type != "BLOB":
                raise ValueError(
                    f"ZIP BLOB source requires a BLOB parameter: {name}"
                )
            folder = str(mapping.get("zip_folder") or "").strip()
            normalized_folder = normalize_path(folder)
            if normalized_folder == ".":
                raise ValueError(
                    f"Select a ZIP folder for BLOB parameter {name}."
                )
            file_type = str(mapping.get("file_type") or "").strip().upper()
            if file_type not in {"FILE", "IMAGE"}:
                raise ValueError(
                    f"Invalid file type for BLOB parameter {name}."
                )
            item.update({
                "zip_folder": normalized_folder,
                "file_type": file_type,
            })
            blob_parameters.add(name)
        elif source in _FILE_SOURCES:
            _, allowed_types = _FILE_SOURCES[source]
            if data_type not in allowed_types:
                raise ValueError(
                    f"{source.replace('_', ' ').lower()} source is not "
                    f"compatible with {name} ({data_type})."
                )
            blob_parameter = str(
                mapping.get("blob_parameter") or ""
            ).strip().upper()
            item["blob_parameter"] = blob_parameter
        else:
            raise ValueError(f"Invalid parameter source for {name}: {source}")

        if source == "ZIP_BLOB" and data_type != "BLOB":
            raise ValueError(f"{name} must be a BLOB parameter.")
        if source not in {"ZIP_BLOB"} and data_type == "BLOB":
            raise ValueError(
                f"BLOB parameter {name} must use a ZIP BLOB source."
            )
        result.append(item)

    for mapping in result:
        if mapping["source"] in _FILE_SOURCES:
            blob_parameter = mapping["blob_parameter"]
            if blob_parameter not in blob_parameters:
                raise ValueError(
                    "File metadata parameter "
                    f"{mapping['parameter']} must reference a mapped "
                    "ZIP BLOB parameter."
                )

    return result


def validate_procedure_records(records, parameters, mappings):
    parameter_by_name = {
        parameter["name"].upper(): parameter
        for parameter in parameters
    }
    mapping_by_name = {
        mapping["parameter"]: mapping
        for mapping in mappings
    }
    errors = []

    for row_number, record in enumerate(records, start=2):
        for name, mapping in mapping_by_name.items():
            if mapping["source"] not in {"EXCEL", "CONSTANT"}:
                continue
            parameter = parameter_by_name[name]
            raw_value = (
                record.get(mapping["excel_column"])
                if mapping["source"] == "EXCEL"
                else mapping["constant"]
            )
            if mapping["source"] == "CONSTANT" and (
                raw_value is None or not str(raw_value).strip()
            ):
                continue
            try:
                validate_value(raw_value, parameter["data_type"])
            except Exception as error:
                errors.append({
                    "row": row_number,
                    "column": name,
                    "error": str(error),
                })
    return errors
