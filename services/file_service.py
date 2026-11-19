
import mimetypes
import re
import zipfile
import stat
from pathlib import PurePosixPath


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif",
    ".bmp", ".webp", ".tif", ".tiff"
}

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".txt",
    ".csv", ".zip"
}


def normalize_path(path):

    path = str(path).replace("\\", "/")

    parsed = PurePosixPath(path)

    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or not parsed.parts
        or ":" in path
    ):
        raise ValueError(
            f"Unsafe file path: {path}"
        )

    return str(parsed)


def inspect_zip(zip_path, config):

    file_index = {}
    files = []
    folders = set()

    total_size = 0
    compressed_size = 0

    with zipfile.ZipFile(zip_path, "r") as archive:

        for info in archive.infolist():

            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(
                    f"Symbolic link is not allowed: {info.filename}"
                )

            if info.is_dir():
                folder = normalize_path(info.filename)
                folders.add(folder)
                continue

            name = normalize_path(info.filename)

            if info.flag_bits & 0x1:
                raise ValueError(
                    f"Encrypted ZIP file is not supported: {name}"
                )

            key = name.casefold()

            if key in file_index:
                raise ValueError(
                    f"Duplicate ZIP filename: {name}"
                )

            if info.file_size > config.MAX_SINGLE_FILE_SIZE:
                raise ValueError(
                    f"File exceeds maximum size: {name}"
                )

            total_size += info.file_size

            compressed_size += info.compress_size

            if total_size > config.MAX_TOTAL_UNCOMPRESSED_SIZE:
                raise ValueError(
                    "ZIP uncompressed size exceeds limit."
                )

            if info.compress_size == 0 and info.file_size > 0:
                raise ValueError(
                    f"Invalid compressed file: {name}"
                )

            if (
                info.compress_size > 0
                and info.file_size / info.compress_size
                    > config.MAX_COMPRESSION_RATIO
            ):
                raise ValueError(
                    f"Suspicious compression ratio: {name}"
                )

            file_index[key] = info.filename

            files.append({
                "name": name,
                "archive_name": info.filename,
                "size": info.file_size,
                "extension": PurePosixPath(name).suffix.lower()
            })

            if len(files) > config.MAX_ZIP_FILES:
                raise ValueError(
                    "ZIP contains too many files."
                )

    return {
        "total_files": len(files),
        "total_size": total_size,
        "compressed_size": compressed_size,
        "files": files,
        "folders": sorted(folders),
        "file_index": file_index
    }


def find_zip_file(file_index, excel_path):

    target = normalize_path(excel_path)

    return file_index.get(target.casefold())


def natural_sort_key(value):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def get_file_metadata(file_info):
    filename = PurePosixPath(file_info["name"]).name
    extension = PurePosixPath(filename).suffix.lower()
    mime_type = (
        mimetypes.guess_type(filename, strict=False)[0]
        or "application/octet-stream"
    )
    return {
        "filename": filename,
        "extension": extension,
        "size": file_info["size"],
        "mime_type": mime_type,
    }


def get_zip_folders(files, explicit_folders=None):
    folders = set(explicit_folders or [])
    for folder in tuple(folders):
        parent = PurePosixPath(folder).parent
        while parent != PurePosixPath("."):
            folders.add(parent.as_posix())
            parent = parent.parent
    for item in files:
        parent = PurePosixPath(item["name"]).parent
        while parent != PurePosixPath("."):
            folders.add(parent.as_posix())
            parent = parent.parent
    return sorted(
        folders,
        key=lambda value: (natural_sort_key(value), value.casefold()),
    )


def review_zip_folders(files, mappings, row_count, explicit_folders=None):
    """Assign naturally sorted files from each mapped folder tree to rows."""
    available_folders = set(get_zip_folders(files, explicit_folders))
    used_folders = set()
    folders = []
    assignment_map = {}
    assignment_metadata = {}
    errors = []

    for mapping in mappings:
        folder = mapping.get("zip_folder", "")
        folder_key = folder.casefold()
        if folder not in available_folders:
            errors.append({
                "row": "-",
                "column": mapping["oracle_column"],
                "error": f"ZIP folder not found: {folder or '(not selected)'}",
            })
            continue
        if any(
            folder_key == used
            or folder_key.startswith(used + "/")
            or used.startswith(folder_key + "/")
            for used in used_folders
        ):
            errors.append({
                "row": "-",
                "column": mapping["oracle_column"],
                "error": (
                    f"ZIP folder overlaps another mapped folder: {folder}"
                ),
            })
            continue
        used_folders.add(folder_key)

        folder_prefix = folder.rstrip("/") + "/"
        candidates = sorted(
            (
                item for item in files
                if item["name"].startswith(folder_prefix)
            ),
            key=lambda item: (
                natural_sort_key(PurePosixPath(item["name"]).name),
                item["name"].casefold(),
            ),
        )
        assigned = candidates[:row_count]
        extra_files = candidates[row_count:]
        invalid_files = [
            item["name"]
            for item in candidates
            if not validate_file_type(item["name"], mapping["column_type"])
            or not (
                get_file_metadata(item)["mime_type"].startswith("image/")
                if mapping["column_type"] == "IMAGE"
                else "/" in get_file_metadata(item)["mime_type"]
            )
        ]
        missing_rows = list(range(len(assigned) + 2, row_count + 2))
        file_assignments = []
        for row_number, item in enumerate(assigned, start=2):
            file_metadata = get_file_metadata(item)
            extension_valid = validate_file_type(
                item["name"],
                mapping["column_type"],
            )
            mime_valid = (
                file_metadata["mime_type"].startswith("image/")
                if mapping["column_type"] == "IMAGE"
                else "/" in file_metadata["mime_type"]
            )
            file_assignments.append({
                "row": row_number,
                "file": item["name"],
                "valid": extension_valid and mime_valid,
                "metadata": file_metadata,
            })
        assignment_map[mapping["oracle_column"]] = {
            row_number: item["archive_name"]
            for row_number, item in enumerate(assigned, start=2)
        }
        assignment_metadata[mapping["oracle_column"]] = {
            assignment["row"]: assignment["metadata"]
            for assignment in file_assignments
        }

        if missing_rows:
            errors.append({
                "row": "-",
                "column": mapping["oracle_column"],
                "error": (
                    f"ZIP folder {folder} has {len(missing_rows)} missing file(s); "
                    f"{row_count} required."
                ),
            })
        if extra_files:
            errors.append({
                "row": "-",
                "column": mapping["oracle_column"],
                "error": (
                    f"ZIP folder {folder} has {len(extra_files)} extra file(s); "
                    f"{row_count} required."
                ),
            })
        if invalid_files:
            errors.append({
                "row": "-",
                "column": mapping["oracle_column"],
                "error": (
                    f"ZIP folder {folder} contains {len(invalid_files)} "
                    f"invalid {mapping['column_type']} file(s)."
                ),
            })

        folders.append({
            "oracle_column": mapping["oracle_column"],
            "column_type": mapping["column_type"],
            "zip_folder": folder,
            "expected_count": row_count,
            "actual_count": len(candidates),
            "assignments": file_assignments,
            "missing_rows": missing_rows,
            "extra_files": [item["name"] for item in extra_files],
            "invalid_files": invalid_files,
        })

    return {
        "folders": folders,
        "assignment_map": assignment_map,
        "assignment_metadata": assignment_metadata,
        "errors": errors,
    }


def read_zip_file(zip_path, internal_path):

    with zipfile.ZipFile(zip_path, "r") as archive:

        return read_zip_member(archive, internal_path)


def read_zip_member(archive, internal_path):

    member = internal_path

    info = archive.getinfo(member)

    if info.file_size > 50 * 1024 * 1024:
        raise ValueError(
            "File exceeds maximum size."
        )

    content = bytearray()
    with archive.open(info, "r") as source:
        while chunk := source.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > 50 * 1024 * 1024:
                raise ValueError("File exceeds maximum size.")
    return bytes(content)


def validate_file_type(file_path, column_type):

    extension = PurePosixPath(
        file_path
    ).suffix.lower()

    if column_type == "IMAGE":
        return extension in IMAGE_EXTENSIONS

    if column_type == "FILE":
        return extension in DOCUMENT_EXTENSIONS

    return False


def get_file_category(extension):

    if extension.lower() in IMAGE_EXTENSIONS:
        return "IMAGE"

    return "FILE"
