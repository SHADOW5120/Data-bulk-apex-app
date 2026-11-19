
import json
import uuid
from pathlib import Path


def generate_id():

    return uuid.uuid4().hex


def save_json(file_path, data):

    path = Path(file_path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_path = path.with_suffix(".tmp")

    temporary_path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            default=str
        ),
        encoding="utf-8"
    )

    temporary_path.replace(path)


def read_json(file_path):

    path = Path(file_path)

    if not path.exists():
        return None

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def get_file_extension(filename):

    return Path(filename).suffix.lower()


def format_file_size(size):

    units = ["B", "KB", "MB", "GB"]

    value = float(size)

    for unit in units:

        if value < 1024 or unit == "GB":
            return f"{value:.2f} {unit}"

        value /= 1024

    return f"{value:.2f} GB"
