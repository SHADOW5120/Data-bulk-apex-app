
import os
import threading
from pathlib import Path

import oracledb
from dotenv import load_dotenv


# ============================================================
# ENVIRONMENT
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE)


class Config:

    # ========================================================
    # FLASK
    # ========================================================

    SECRET_KEY = os.getenv("FLASK_SECRET_KEY")

    DEBUG = (
        os.getenv("FLASK_DEBUG", "false").lower() == "true"
    )

    # ========================================================
    # ORACLE DATABASE
    # ========================================================

    DB_HOST = os.getenv(
        "DB_HOST", "192.168.77.24"
    ).strip()

    DB_PORT = int(
        os.getenv("DB_PORT", "1521")
    )

    DB_SERVICE = os.getenv(
        "DB_SERVICE", "orcl1"
    ).strip()

    DB_USER = os.getenv(
        "DB_USER", ""
    ).strip()

    DB_PASSWORD = os.getenv(
        "DB_PASSWORD", ""
    )

    DB_SCHEMA = os.getenv(
        "DB_SCHEMA", "APPS"
    ).strip().upper()

    DB_CONNECT_TIMEOUT = int(
        os.getenv("DB_CONNECT_TIMEOUT", "15")
    )

    # Oracle DSN
    DB_DSN = (
        f"{DB_HOST}:{DB_PORT}/{DB_SERVICE}"
    )

    # Oracle Instant Client
    ORACLE_CLIENT_LIB_DIR = os.getenv(
        "ORACLE_CLIENT_LIB_DIR", ""
    ).strip()

    # ========================================================
    # UPLOAD DIRECTORIES
    # ========================================================

    UPLOAD_ROOT = BASE_DIR / "uploads"

    EXCEL_FOLDER = UPLOAD_ROOT / "excel"

    ARCHIVE_FOLDER = UPLOAD_ROOT / "archives"

    EXTRACTED_FOLDER = UPLOAD_ROOT / "extracted"

    JOB_FOLDER = UPLOAD_ROOT / "jobs"

    # ========================================================
    # UPLOAD LIMITS
    # ========================================================

    MAX_CONTENT_LENGTH = (
        1024 * 1024 * 1024
    )

    MAX_EXCEL_SIZE = (
        50 * 1024 * 1024
    )

    MAX_ZIP_SIZE = (
        950 * 1024 * 1024
    )

    MAX_ZIP_FILES = 5000

    MAX_SINGLE_FILE_SIZE = (
        50 * 1024 * 1024
    )

    MAX_TOTAL_UNCOMPRESSED_SIZE = (
        2 * 1024 * 1024 * 1024
    )

    MAX_COMPRESSION_RATIO = 200

    # ========================================================
    # EXCEL
    # ========================================================

    PREVIEW_LIMIT = 30

    # ========================================================
    # IMPORT
    # ========================================================

    IMPORT_BATCH_SIZE = 50

    MAX_BLOB_BATCH_BYTES = (
        32 * 1024 * 1024
    )

    # ========================================================
    # ALLOWED FILE EXTENSIONS
    # ========================================================

    ALLOWED_EXCEL = {"xlsx"}

    ALLOWED_ARCHIVE = {"zip"}


# ============================================================
# ORACLE CLIENT INITIALIZATION
# ============================================================

_oracle_init_lock = threading.Lock()
_oracle_initialized = False


def initialize_oracle_client():
    """
    Initialize Oracle Instant Client in Thick Mode.
    Must be called before creating any Oracle connection.
    """

    global _oracle_initialized

    with _oracle_init_lock:

        if _oracle_initialized:
            return

        if not Config.ORACLE_CLIENT_LIB_DIR:
            raise RuntimeError(
                "ORACLE_CLIENT_LIB_DIR is missing in .env"
            )

        client_path = Path(
            Config.ORACLE_CLIENT_LIB_DIR
        )

        if not client_path.is_dir():
            raise RuntimeError(
                f"Oracle Client directory not found: {client_path}"
            )

        if not (client_path / "oci.dll").is_file():
            raise RuntimeError(
                f"oci.dll not found in: {client_path}"
            )

        try:

            # Initialize Thick Mode only if still in Thin Mode
            if oracledb.is_thin_mode():

                oracledb.init_oracle_client(
                    lib_dir=str(client_path)
                )

            if oracledb.is_thin_mode():
                raise RuntimeError(
                    "Oracle is still running in Thin Mode."
                )

            _oracle_initialized = True

            print(
                f"[ORACLE] Client initialized: "
                f"{oracledb.clientversion()}"
            )

            print(
                "[ORACLE] Thick Mode enabled."
            )

        except Exception as error:

            raise RuntimeError(
                f"Cannot initialize Oracle Client: {error}"
            ) from error


# ============================================================
# CREATE UPLOAD DIRECTORIES
# ============================================================

for directory in [
    Config.EXCEL_FOLDER,
    Config.ARCHIVE_FOLDER,
    Config.EXTRACTED_FOLDER,
    Config.JOB_FOLDER
]:

    directory.mkdir(
        parents=True,
        exist_ok=True
    )


# ============================================================
# CONFIGURATION VALIDATION
# ============================================================

if not Config.SECRET_KEY:
    print("[WARNING] FLASK_SECRET_KEY is missing.")

if not Config.DB_USER:
    print("[WARNING] DB_USER is missing.")

if not Config.DB_PASSWORD:
    print("[WARNING] DB_PASSWORD is missing.")
