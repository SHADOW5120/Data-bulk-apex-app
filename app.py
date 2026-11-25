
import re
from pathlib import Path

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
    jsonify,
)

from config import Config

from services.excel_service import (
    get_sheet_names,
    read_excel_data,
    read_excel_records
)

from services.file_service import (
    get_zip_folders,
    inspect_zip,
    review_zip_folders,
)

from services.oracle_service import OracleService

from services.mapping_service import (
    build_mapping,
    get_blob_mappings
)

from services.validation_service import (
    get_primary_key_rows,
    validate_file_metadata,
    validate_records,
)

from services.import_service import import_records
from services.procedure_import_service import import_procedure_records
from services.procedure_service import (
    validate_procedure_mappings,
    validate_procedure_records,
)

from utils.helpers import (
    generate_id,
    save_json,
    read_json
)


# ============================================================
# 1. APPLICATION INITIALIZATION
# ============================================================

app = Flask(__name__)

app.config.from_object(Config)

if not Config.SECRET_KEY:
    raise RuntimeError(
        "FLASK_SECRET_KEY is not configured in .env"
    )

oracle = OracleService(Config)


# ============================================================
# 2. JOB MANAGEMENT
# ============================================================

def get_job_path(import_id):

    if not re.fullmatch(r"[a-f0-9]{32}", import_id):
        abort(400, "Invalid import ID.")

    return Config.JOB_FOLDER / f"{import_id}.json"


def get_job(import_id):

    job_path = get_job_path(import_id)

    job = read_json(job_path)

    if not job:
        abort(404, "Import job not found.")

    return job


def save_job(import_id, job):

    save_json(
        get_job_path(import_id),
        job
    )


def get_job_files(job):

    """
    Return:
        excel_path: Required Excel file.
        zip_path: Optional ZIP file (None if not uploaded).
    """

    # --------------------------------------------------------
    # Excel file
    # --------------------------------------------------------

    excel_path = Path(
        job["excel_path"]
    ).resolve()

    excel_folder = Config.EXCEL_FOLDER.resolve()

    if not excel_path.is_relative_to(excel_folder):
        abort(400, "Invalid Excel file path.")

    if not excel_path.is_file():
        abort(404, "Excel file not found.")

    # --------------------------------------------------------
    # ZIP file (OPTIONAL)
    # --------------------------------------------------------

    zip_path = None

    if job.get("zip_path"):

        zip_path = Path(
            job["zip_path"]
        ).resolve()

        archive_folder = Config.ARCHIVE_FOLDER.resolve()

        if not zip_path.is_relative_to(archive_folder):
            abort(400, "Invalid ZIP file path.")

        if not zip_path.is_file():
            abort(404, "ZIP file not found.")

    return excel_path, zip_path


def get_zip_information(zip_path):

    """
    Inspect ZIP only when a ZIP file exists.
    Excel-only import returns an empty file index.
    """

    if zip_path is None:

        return {
            "total_files": 0,
            "total_size": 0,
            "files": [],
            "folders": [],
            "file_index": {}
        }

    return inspect_zip(
        zip_path,
        Config
    )


# ============================================================
# 3. HOME PAGE
# ============================================================

@app.route("/")
def index():

    return render_template("index.html")


# ============================================================
# 4. UPLOAD EXCEL AND OPTIONAL ZIP
# ============================================================

@app.route("/start", methods=["POST"])
def start_upload():

    excel = request.files.get("excel_file")

    archive = request.files.get("zip_file")

    # --------------------------------------------------------
    # Check Excel
    # --------------------------------------------------------

    if not excel or not excel.filename:

        flash("Please select an Excel file.")

        return redirect(url_for("index"))

    if not excel.filename.lower().endswith(".xlsx"):

        flash("Only XLSX files are supported.")

        return redirect(url_for("index"))

    # --------------------------------------------------------
    # Check optional ZIP
    # --------------------------------------------------------

    has_zip = (
        archive is not None
        and archive.filename != ""
    )

    if has_zip:

        if not archive.filename.lower().endswith(".zip"):

            flash("Only ZIP files are supported.")

            return redirect(url_for("index"))

    # --------------------------------------------------------
    # Generate import ID
    # --------------------------------------------------------

    import_id = generate_id()

    excel_path = (
        Config.EXCEL_FOLDER
        / f"{import_id}.xlsx"
    )

    zip_path = None

    try:

        # ----------------------------------------------------
        # Save Excel
        # ----------------------------------------------------

        excel.save(excel_path)

        if excel_path.stat().st_size > Config.MAX_EXCEL_SIZE:

            raise ValueError(
                "Excel file exceeds the maximum allowed size."
            )

        # ----------------------------------------------------
        # Read Excel worksheets
        # ----------------------------------------------------

        sheets = get_sheet_names(excel_path)

        if not sheets:

            raise ValueError(
                "Excel file does not contain any worksheets."
            )

        # ----------------------------------------------------
        # Save and inspect ZIP (if provided)
        # ----------------------------------------------------

        zip_info = get_zip_information(None)

        if has_zip:

            zip_path = (
                Config.ARCHIVE_FOLDER
                / f"{import_id}.zip"
            )

            archive.save(zip_path)

            if zip_path.stat().st_size > Config.MAX_ZIP_SIZE:

                raise ValueError(
                    "ZIP file exceeds the maximum allowed size."
                )

            zip_info = inspect_zip(
                zip_path,
                Config
            )

        # ----------------------------------------------------
        # Create import job
        # ----------------------------------------------------

        job = {

            "import_id": import_id,

            "excel_path": str(
                excel_path.resolve()
            ),

            "zip_path": (
                str(zip_path.resolve())
                if zip_path
                else None
            ),

            "has_zip": has_zip,

            "sheets": sheets,

            "selected_sheet": sheets[0],

            "zip_count": zip_info["total_files"],

            "zip_size": zip_info["total_size"],

            "table_name": None,

            "metadata": None,

            "import_mode": "DIRECT",

            "procedure": None,

            "procedure_metadata": None,

            "mappings": [],

            "status": "UPLOADED",

            "result": None

        }

        save_job(
            import_id,
            job
        )

        return redirect(
            url_for(
                "preview",
                import_id=import_id
            )
        )

    except Exception as error:

        # Clean up uploaded files if initialization fails

        excel_path.unlink(missing_ok=True)

        if zip_path:
            zip_path.unlink(missing_ok=True)

        flash(str(error))

        return redirect(url_for("index"))


# ============================================================
# 5. EXCEL PREVIEW
# ============================================================

@app.route("/preview/<import_id>")
def preview(import_id):

    job = get_job(import_id)

    excel_path, zip_path = get_job_files(job)

    # --------------------------------------------------------
    # Select worksheet
    # --------------------------------------------------------

    selected_sheet = request.args.get(
        "sheet",
        job["selected_sheet"]
    )

    if selected_sheet not in job["sheets"]:

        abort(400, "Invalid worksheet.")

    # --------------------------------------------------------
    # Read preview
    # --------------------------------------------------------

    data = read_excel_data(
        excel_path,
        selected_sheet,
        Config.PREVIEW_LIMIT
    )

    job["selected_sheet"] = selected_sheet

    save_job(
        import_id,
        job
    )

    return render_template(
        "preview.html",
        job=job,
        data=data,
        selected_sheet=selected_sheet
    )


# ============================================================
# 6. ORACLE TABLE SELECTION AND COLUMN MAPPING
# ============================================================

@app.route(
    "/mapping/<import_id>",
    methods=["GET", "POST"]
)
def mapping(import_id):

    job = get_job(import_id)

    excel_path, zip_path = get_job_files(job)
    is_ajax_request = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
    )

    # --------------------------------------------------------
    # Load selected Oracle table
    # --------------------------------------------------------

    if request.method == "POST":

        try:
            import_mode = request.form.get("import_mode", "DIRECT").upper()
            if import_mode == "DIRECT":
                table_name = request.form.get("table_name", "").strip()
                if not table_name:
                    raise ValueError("Please select an Oracle table.")
                metadata = oracle.get_table_metadata(table_name)
                job["table_name"] = table_name
                job["metadata"] = metadata
                job["procedure"] = None
                job["procedure_metadata"] = None
            elif import_mode == "PROCEDURE":
                procedure_key = request.form.get("procedure_key", "")
                parts = procedure_key.split("|")
                if len(parts) != 3:
                    raise ValueError("Please select an Oracle procedure.")
                package_name, procedure_name, subprogram_id = parts
                metadata = oracle.get_procedure_metadata(
                    package_name or None,
                    procedure_name,
                    subprogram_id,
                )
                job["import_mode"] = "PROCEDURE"
                job["table_name"] = None
                job["metadata"] = None
                job["procedure"] = {
                    "package_name": metadata["package_name"],
                    "procedure_name": metadata["procedure_name"],
                    "subprogram_id": metadata["subprogram_id"],
                    "overload": metadata["overload"],
                }
                job["procedure_metadata"] = metadata
            else:
                raise ValueError("Invalid import mode.")

            job["import_mode"] = import_mode
            job["mappings"] = []

            job["status"] = "MAPPING"

            save_job(
                import_id,
                job
            )

            if import_mode == "PROCEDURE":
                if is_ajax_request:
                    return jsonify({
                        "success": True,
                        "redirect_url": url_for(
                            "mapping",
                            import_id=import_id,
                        ),
                    })
                return redirect(
                    url_for("mapping", import_id=import_id)
                )

        except Exception as error:
            app.logger.exception(
                "Oracle metadata loading failed for mapping import %s",
                import_id,
            )

            if is_ajax_request:
                status_code = 400 if isinstance(error, ValueError) else 503
                return jsonify({
                    "success": False,
                    "error": str(error),
                }), status_code

            flash(str(error))

            return redirect(
                url_for(
                    "mapping",
                    import_id=import_id
                )
            )

    try:
        if (
            job.get("import_mode") == "PROCEDURE"
            and job.get("procedure")
            and job.get("procedure_metadata")
        ):
            table_page = {"tables": [], "has_more": False}
            tables = []
            procedure = job["procedure"]
            procedure_label = (
                f"{procedure['package_name']}."
                if procedure.get("package_name")
                else ""
            ) + procedure["procedure_name"]
            if procedure.get("overload"):
                procedure_label += f" (overload {procedure['overload']})"
            procedure_page = {
                "procedures": [{
                    **procedure,
                    "label": procedure_label,
                }],
                "has_more": False,
            }
        else:
            table_page = oracle.search_tables(page=1, page_size=50)
            tables = table_page["tables"]
            procedure_page = {
                "procedures": [],
                "has_more": False,
            }
    except Exception as error:
        app.logger.exception(
            "Oracle table discovery failed for mapping import %s",
            import_id,
        )
        return render_template(
            "mapping.html",
            job=job,
            tables=[],
            metadata=job.get("metadata"),
            columns=[],
            zip_folders=[],
            table_has_more=False,
            procedures=[],
            procedure_has_more=False,
            error=str(error),
        ), 503

    # --------------------------------------------------------
    # Read Excel columns
    # --------------------------------------------------------

    data = read_excel_data(
        excel_path,
        job["selected_sheet"],
        Config.PREVIEW_LIMIT
    )
    zip_info = get_zip_information(zip_path)

    return render_template(
        "mapping.html",
        job=job,
        tables=tables,
        metadata=job.get("metadata"),
        procedures=procedure_page["procedures"],
        procedure_has_more=procedure_page["has_more"],
        columns=data["columns"],
        table_has_more=table_page["has_more"],
        zip_folders=get_zip_folders(
            zip_info["files"],
            zip_info["folders"],
        ),
        error=None
    )


@app.route("/mapping/<import_id>/tables")
def search_oracle_tables(import_id):
    get_job(import_id)
    try:
        result = oracle.search_tables(
            query=request.args.get("q", ""),
            page=request.args.get("page", 1, type=int),
            page_size=50,
        )
        return jsonify(result)
    except Exception as error:
        app.logger.exception(
            "Oracle table search failed for mapping import %s",
            import_id,
        )
        return jsonify({"error": str(error)}), 503


@app.route("/mapping/<import_id>/procedures")
def search_oracle_procedures(import_id):
    get_job(import_id)
    try:
        result = oracle.search_procedures(
            query=request.args.get("q", ""),
            page=request.args.get("page", 1, type=int),
            page_size=50,
        )
        return jsonify(result)
    except Exception as error:
        app.logger.exception(
            "Oracle procedure search failed for mapping import %s",
            import_id,
        )
        return jsonify({"error": str(error)}), 503


def _refresh_procedure_metadata(job):
    procedure = job.get("procedure")
    if not procedure:
        raise ValueError("Select an Oracle procedure first.")
    metadata = oracle.get_procedure_metadata(
        procedure.get("package_name"),
        procedure.get("procedure_name"),
        procedure.get("subprogram_id"),
    )
    saved = job.get("procedure_metadata") or {}
    if metadata.get("parameters") != saved.get("parameters"):
        raise ValueError(
            "The selected procedure signature changed. Reload the procedure "
            "and review its parameter mappings again."
        )
    return metadata


def _procedure_blob_mappings(mappings):
    return [
        {
            "oracle_column": mapping["parameter"],
            "column_type": mapping["file_type"],
            "zip_folder": mapping["zip_folder"],
        }
        for mapping in mappings
        if mapping["source"] == "ZIP_BLOB"
    ]


def _procedure_mapping_errors(job, message):
    return render_template(
        "review.html",
        job=job,
        mappings=job.get("mappings", []),
        total_rows=0,
        errors=[{
            "row": "-",
            "operation": "PROCEDURE",
            "column": "Oracle",
            "error": message,
        }],
        valid=False,
        zip_review={"folders": [], "errors": []},
        selected_actions={},
        procedure_metadata=job.get("procedure_metadata"),
    )


def _review_procedure_import(import_id, job, excel_path, zip_path):
    try:
        metadata = _refresh_procedure_metadata(job)
        parameter_names = request.form.getlist("procedure_parameter")
        sources = request.form.getlist("parameter_source")
        requirements = request.form.getlist("parameter_requirement")
        excel_columns = request.form.getlist("parameter_excel_column")
        constants = request.form.getlist("parameter_constant")
        zip_folders = request.form.getlist("parameter_zip_folder")
        file_types = request.form.getlist("parameter_file_type")
        blob_parameters = request.form.getlist("parameter_blob_parameter")
        if not (
            len(parameter_names)
            == len(sources)
            == len(requirements)
            == len(excel_columns)
            == len(constants)
            == len(zip_folders)
            == len(file_types)
            == len(blob_parameters)
        ):
            raise ValueError("Invalid procedure parameter mapping form.")
        if any(
            requirement not in {"OPTIONAL", "REQUIRED"}
            for requirement in requirements
        ):
            raise ValueError("Invalid procedure parameter requirement.")

        selected_mappings = [
            {
                "parameter": name,
                "source": source,
                "mapping_required": requirement == "REQUIRED",
                "excel_column": excel_column,
                "constant": constant,
                "zip_folder": zip_folder,
                "file_type": file_type,
                "blob_parameter": blob_parameter,
            }
            for name, source, requirement, excel_column, constant, zip_folder,
            file_type, blob_parameter in zip(
                parameter_names,
                sources,
                requirements,
                excel_columns,
                constants,
                zip_folders,
                file_types,
                blob_parameters,
            )
        ]
        excel_data = read_excel_data(
            excel_path,
            job["selected_sheet"],
        )
        mappings = validate_procedure_mappings(
            metadata["parameters"],
            selected_mappings,
            excel_data["columns"],
        )
        records = read_excel_records(excel_path, job["selected_sheet"])
        zip_info = get_zip_information(zip_path)
        blob_mappings = _procedure_blob_mappings(mappings)
        zip_review = review_zip_folders(
            zip_info["files"],
            blob_mappings,
            len(records),
            zip_info["folders"],
        )
        if blob_mappings and not zip_path:
            zip_review["errors"].append({
                "row": "-",
                "column": "ZIP",
                "error": "A ZIP file is required for mapped BLOB parameters.",
            })

        errors = validate_procedure_records(
            records,
            metadata["parameters"],
            mappings,
        )
        errors.extend(zip_review["errors"])
        for error in errors:
            error.setdefault("operation", "PROCEDURE")

        job["procedure_metadata"] = metadata
        job["mappings"] = mappings
        job["status"] = "REVIEWED" if not errors else "INVALID"
        save_job(import_id, job)
        return render_template(
            "review.html",
            job=job,
            mappings=mappings,
            total_rows=len(records),
            errors=errors,
            valid=not errors,
            zip_review=zip_review,
            selected_actions={},
            procedure_metadata=metadata,
        )
    except Exception as error:
        app.logger.exception(
            "Procedure mapping validation failed for import %s",
            import_id,
        )
        return _procedure_mapping_errors(job, str(error)), 400


# ============================================================
# 7. REVIEW MAPPING AND VALIDATE DATA
# ============================================================

@app.route(
    "/review/<import_id>",
    methods=["POST"]
)
def review(import_id):

    job = get_job(import_id)

    excel_path, zip_path = get_job_files(job)

    if job.get("import_mode") == "PROCEDURE":
        return _review_procedure_import(
            import_id,
            job,
            excel_path,
            zip_path,
        )

    metadata = job.get("metadata")

    if not metadata:

        abort(400, "Please select an Oracle table first.")

    # --------------------------------------------------------
    # Read mapping form
    # --------------------------------------------------------

    excel_columns = request.form.getlist(
        "excel_column"
    )

    oracle_columns = request.form.getlist(
        "oracle_column"
    )

    mapping_types = request.form.getlist(
        "column_type"
    )

    blob_columns = request.form.getlist("blob_column")
    blob_types = request.form.getlist("blob_type")
    blob_folders = request.form.getlist("blob_folder")
    filename_columns = request.form.getlist("blob_filename_column")
    extension_columns = request.form.getlist("blob_extension_column")
    size_columns = request.form.getlist("blob_size_column")
    mime_columns = request.form.getlist("blob_mime_column")

    if not (
        len(excel_columns)
        == len(oracle_columns)
        == len(mapping_types)
    ):

        abort(400, "Invalid mapping form.")
    if not (
        len(blob_columns)
        == len(blob_types)
        == len(blob_folders)
        == len(filename_columns)
        == len(extension_columns)
        == len(size_columns)
        == len(mime_columns)
    ):
        abort(400, "Invalid BLOB mapping form.")

    selected_mappings = []

    for excel_col, oracle_col, mapping_type in zip(
        excel_columns,
        oracle_columns,
        mapping_types
    ):

        if not oracle_col:
            continue

        selected_mappings.append({
            "excel_column": excel_col,
            "oracle_column": oracle_col,
            "column_type": mapping_type,
        })

    for (
        oracle_col,
        mapping_type,
        zip_folder,
        filename_column,
        extension_column,
        size_column,
        mime_column,
    ) in zip(
        blob_columns,
        blob_types,
        blob_folders,
        filename_columns,
        extension_columns,
        size_columns,
        mime_columns,
    ):
        if not oracle_col or not zip_folder:
            continue
        selected_mappings.append({
            "excel_column": "",
            "oracle_column": oracle_col,
            "column_type": mapping_type,
            "zip_folder": zip_folder,
            "metadata_columns": {
                "filename": filename_column,
                "extension": extension_column,
                "size": size_column,
                "mime_type": mime_column,
            },
        })

    try:

        # ----------------------------------------------------
        # Build mapping
        # ----------------------------------------------------

        excel_data = read_excel_data(
            excel_path,
            job["selected_sheet"]
        )

        mappings = build_mapping(
            excel_data["columns"],
            metadata,
            selected_mappings
        )

        if not mappings:

            raise ValueError(
                "Please select at least one column."
            )

        # ----------------------------------------------------
        # Check BLOB requirement
        # ----------------------------------------------------

        blob_mappings = get_blob_mappings(
            mappings
        )

        # ----------------------------------------------------
        # Read all Excel records
        # ----------------------------------------------------

        records = read_excel_records(
            excel_path,
            job["selected_sheet"]
        )

        # ----------------------------------------------------
        # Inspect ZIP and build folder-to-row assignments
        # ----------------------------------------------------

        zip_info = get_zip_information(
            zip_path
        )
        zip_review = review_zip_folders(
            zip_info["files"],
            blob_mappings,
            len(records),
            zip_info["folders"],
        )
        if blob_mappings and not zip_path:
            zip_review["errors"].append({
                "row": "-",
                "column": "ZIP",
                "error": "A ZIP file is required for mapped BLOB columns.",
            })

        errors = []
        row_operations = None
        blob_state_snapshot = {}
        if blob_mappings:
            row_operations = {
                row_number: "UNVERIFIED"
                for row_number in range(2, len(records) + 2)
            }
            try:
                primary_keys, key_rows = get_primary_key_rows(
                    records,
                    metadata,
                    mappings,
                )
                blob_states = oracle.get_blob_states(
                    job["table_name"],
                    primary_keys,
                    key_rows,
                    [item["oracle_column"] for item in blob_mappings],
                )
                row_operations = {
                    row_number: (
                        "INSERT" if state is None else "UPDATE"
                    )
                    for row_number, state in blob_states.items()
                }
                blob_state_snapshot = {
                    str(row_number): state
                    for row_number, state in blob_states.items()
                }
                for folder in zip_review["folders"]:
                    folder_mapping = next(
                        item for item in blob_mappings
                        if item["oracle_column"] == folder["oracle_column"]
                    )
                    for assignment in folder["assignments"]:
                        file_metadata = assignment["metadata"]
                        assignment["metadata_columns"] = {
                            field: file_metadata[field]
                            for field in folder_mapping.get(
                                "metadata_columns", {}
                            )
                        }
                        state = blob_states[assignment["row"]]
                        if state is None:
                            assignment["status"] = "INSERT"
                        elif state[folder["oracle_column"]]:
                            assignment["status"] = "UPDATE"
                        else:
                            assignment["status"] = "SKIP"
            except ValueError as error:
                errors.append({
                    "row": "-",
                    "column": "PRIMARY KEY",
                    "error": str(error),
                })
            except Exception as error:
                app.logger.exception(
                    "Oracle BLOB state lookup failed for import %s",
                    import_id,
                )
                errors.append({
                    "row": "-",
                    "column": "Oracle",
                    "error": f"Could not inspect existing BLOB values: {error}",
                })

        # ----------------------------------------------------
        # Validate
        # ----------------------------------------------------

        errors.extend(validate_records(
            records,
            metadata,
            mappings,
            zip_info["file_index"],
            row_operations=row_operations,
        ))
        errors.extend(
            validate_file_metadata(zip_review, mappings, metadata)
        )
        errors.extend(zip_review["errors"])
        for error in errors:
            row_number = error.get("row")
            if isinstance(row_number, int) and "operation" not in error:
                error["operation"] = (
                    (row_operations or {}).get(
                        row_number,
                        "INSERT" if not blob_mappings else "UNVERIFIED",
                    )
                )

        # ----------------------------------------------------
        # Save review information
        # ----------------------------------------------------

        job["mappings"] = mappings
        if blob_mappings:
            job["blob_state_snapshot"] = blob_state_snapshot
        else:
            job.pop("blob_state_snapshot", None)

        job["status"] = (
            "REVIEWED"
            if not errors
            else "INVALID"
        )

        save_job(
            import_id,
            job
        )

        return render_template(
            "review.html",

            job=job,

            mappings=mappings,

            total_rows=len(records),

            errors=errors,

            valid=(len(errors) == 0),

            zip_review=zip_review,

            selected_actions={},

        )

    except Exception as error:

        flash(str(error))

        return redirect(
            url_for(
                "mapping",
                import_id=import_id
            )
        )


def _upload_procedure_import(import_id, job):
    records = []
    try:
        excel_path, zip_path = get_job_files(job)
        records = read_excel_records(
            excel_path,
            job["selected_sheet"],
        )
        metadata = _refresh_procedure_metadata(job)
        excel_columns = list(records[0].keys()) if records else (
            read_excel_data(excel_path, job["selected_sheet"])["columns"]
        )
        mappings = validate_procedure_mappings(
            metadata["parameters"],
            job.get("mappings", []),
            excel_columns,
        )
        errors = validate_procedure_records(
            records,
            metadata["parameters"],
            mappings,
        )

        zip_info = get_zip_information(zip_path)
        blob_mappings = _procedure_blob_mappings(mappings)
        zip_review = review_zip_folders(
            zip_info["files"],
            blob_mappings,
            len(records),
            zip_info["folders"],
        )
        if blob_mappings and not zip_path:
            zip_review["errors"].append({
                "row": "-",
                "column": "ZIP",
                "error": "A ZIP file is required for mapped BLOB parameters.",
            })
        errors.extend(zip_review["errors"])
        for error in errors:
            error.setdefault("operation", "PROCEDURE")

        if errors:
            job["status"] = "INVALID"
            save_job(import_id, job)
            return render_template(
                "review.html",
                job=job,
                mappings=mappings,
                total_rows=len(records),
                errors=errors,
                valid=False,
                zip_review=zip_review,
                selected_actions={},
                procedure_metadata=metadata,
            )

        job["status"] = "IMPORTING"
        save_job(import_id, job)
        result = import_procedure_records(
            records=records,
            zip_path=zip_path,
            mappings=mappings,
            procedure=metadata,
            oracle_service=oracle,
            assignment_map=zip_review["assignment_map"],
            assignment_metadata=zip_review["assignment_metadata"],
        )
        job["status"] = result["status"]
        job["result"] = result
        save_job(import_id, job)
        return redirect(url_for("result", import_id=import_id))
    except Exception as error:
        app.logger.exception(
            "Oracle procedure import failed for import %s",
            import_id,
        )
        job["status"] = "FAILED"
        job["result"] = {
            "status": "FAILED",
            "total_rows": len(records),
            "success_rows": 0,
            "failed_rows": len(records),
            "success_files": 0,
            "failed_files": 0,
            "errors": [{"error": str(error)}],
        }
        save_job(import_id, job)
        return redirect(url_for("result", import_id=import_id))


# ============================================================
# 8. UPLOAD DATA TO ORACLE
# ============================================================

@app.route(
    "/upload/<import_id>",
    methods=["POST"]
)
def upload_to_oracle(import_id):

    job = get_job(import_id)

    # --------------------------------------------------------
    # Check job status
    # --------------------------------------------------------

    if job["status"] != "REVIEWED":

        abort(
            400,
            "Please complete a valid REVIEW first."
        )

    if job.get("import_mode") == "PROCEDURE":
        return _upload_procedure_import(import_id, job)

    excel_path, zip_path = get_job_files(job)

    try:

        # ----------------------------------------------------
        # Load Excel records
        # ----------------------------------------------------

        records = read_excel_records(
            excel_path,
            job["selected_sheet"]
        )

        # ----------------------------------------------------
        # Load ZIP information if available
        # ----------------------------------------------------

        zip_info = get_zip_information(
            zip_path
        )

        file_index = zip_info["file_index"]

        # ----------------------------------------------------
        # Check BLOB mapping again
        # ----------------------------------------------------

        blob_mappings = get_blob_mappings(
            job["mappings"]
        )

        folder_blob_mappings = [
            item for item in blob_mappings if "zip_folder" in item
        ]
        zip_review = review_zip_folders(
            zip_info["files"],
            folder_blob_mappings,
            len(records),
            zip_info["folders"],
        )
        if folder_blob_mappings and not zip_path:
            zip_review["errors"].append({
                "row": "-",
                "column": "ZIP",
                "error": "A ZIP file is required for mapped BLOB columns.",
            })
        expected_blob_states = job.get("blob_state_snapshot", {})
        row_operations = None
        if folder_blob_mappings:
            row_operations = {
                row_number: (
                    "INSERT"
                    if expected_blob_states.get(str(row_number)) is None
                    else "UPDATE"
                )
                for row_number in range(2, len(records) + 2)
            }
        errors = validate_file_metadata(
            zip_review,
            folder_blob_mappings,
            job["metadata"],
        )

        # ----------------------------------------------------
        # Validate again before actual import
        # ----------------------------------------------------

        errors.extend(validate_records(
            records,
            job["metadata"],
            job["mappings"],
            file_index,
            row_operations=row_operations,
        ))
        errors.extend(zip_review["errors"])
        for error in errors:
            row_number = error.get("row")
            if isinstance(row_number, int) and "operation" not in error:
                error["operation"] = (
                    (row_operations or {}).get(
                        row_number,
                        "INSERT" if not folder_blob_mappings else "UNVERIFIED",
                    )
                )

        if errors:

            job["status"] = "INVALID"

            save_job(
                import_id,
                job
            )

            return render_template(
                "review.html",
                job=job,
                mappings=job["mappings"],
                total_rows=len(records),
                errors=errors,
                valid=False,
                zip_review=zip_review,
                selected_actions={},
            )

        primary_keys = []
        key_rows = []
        blob_actions = {}
        if folder_blob_mappings:
            primary_keys, key_rows = get_primary_key_rows(
                records,
                job["metadata"],
                job["mappings"],
            )
            for folder in zip_review["folders"]:
                oracle_column = folder["oracle_column"]
                for assignment in folder["assignments"]:
                    row_number = assignment["row"]
                    row_state = expected_blob_states.get(str(row_number))
                    if row_state is None or row_state.get(oracle_column, True):
                        continue
                    action_key = (
                        f"blob_action_{row_number}_{oracle_column}"
                    )
                    action = request.form.get(action_key, "SKIP").upper()
                    if action not in {"REPLACE", "SKIP"}:
                        raise ValueError(
                            f"Invalid action for Excel row {row_number}, "
                            f"Oracle column {oracle_column}."
                        )
                    blob_actions.setdefault(row_number, {})[
                        oracle_column
                    ] = action

        # ----------------------------------------------------
        # Set importing status
        # ----------------------------------------------------

        job["status"] = "IMPORTING"

        save_job(
            import_id,
            job
        )

        # ----------------------------------------------------
        # Execute import
        # ----------------------------------------------------

        result = import_records(

            records=records,

            zip_path=zip_path,

            file_index=file_index,

            table_name=job["table_name"],

            metadata=job["metadata"],

            mappings=job["mappings"],

            oracle_service=oracle,

            assignment_map=zip_review["assignment_map"],

            assignment_metadata=zip_review["assignment_metadata"],

            primary_keys=primary_keys,

            key_rows=key_rows,

            expected_blob_states=expected_blob_states,

            blob_actions=blob_actions,

        )

        # ----------------------------------------------------
        # Save result
        # ----------------------------------------------------

        job["status"] = result["status"]

        job["result"] = result

        save_job(
            import_id,
            job
        )

        return redirect(
            url_for(
                "result",
                import_id=import_id
            )
        )

    except Exception as error:
        app.logger.exception(
            "Oracle import failed for import %s",
            import_id,
        )

        # Do not overwrite a previously saved import result

        if job.get("status") != "INVALID":

            job["status"] = "FAILED"

            job["result"] = {

                "status": "FAILED",

                "total_rows": len(records) if "records" in locals() else 0,

                "success_rows": 0,

                "failed_rows": len(records) if "records" in locals() else 0,

                "success_files": 0,

                "failed_files": 0,

                "errors": [{
                    "error": str(error)
                }]

            }

            save_job(
                import_id,
                job
            )

        return redirect(
            url_for(
                "result",
                import_id=import_id
            )
        )


# ============================================================
# 9. IMPORT RESULT
# ============================================================

@app.route("/result/<import_id>")
def result(import_id):

    job = get_job(import_id)

    return render_template(
        "result.html",
        job=job,
        result=job.get("result") or {}
    )


# ============================================================
# 10. ERROR HANDLERS
# ============================================================

@app.errorhandler(413)
def request_entity_too_large(error):

    flash(
        "Uploaded files exceed the maximum request size."
    )

    return redirect(
        url_for("index")
    )


# ============================================================
# 11. RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=Config.DEBUG
    )
