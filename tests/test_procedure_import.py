import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from services.oracle_service import OracleService
from services.procedure_import_service import import_procedure_records
from services.procedure_service import (
    validate_procedure_mappings,
    validate_procedure_records,
)


class ProcedureMappingTests(unittest.TestCase):
    def setUp(self):
        self.parameters = [
            {
                "name": "P_ID",
                "data_type": "NUMBER",
                "mode": "IN",
                "defaulted": False,
                "nullable": None,
            },
            {
                "name": "P_OPTIONAL",
                "data_type": "VARCHAR2",
                "mode": "IN",
                "defaulted": True,
                "nullable": None,
            },
            {
                "name": "P_DOCUMENT",
                "data_type": "BLOB",
                "mode": "IN",
                "defaulted": False,
            },
            {
                "name": "P_FILENAME",
                "data_type": "VARCHAR2",
                "mode": "IN",
                "defaulted": False,
            },
            {
                "name": "P_RESULT",
                "data_type": "VARCHAR2",
                "mode": "OUT",
                "defaulted": False,
            },
        ]

    def test_accepts_excel_constants_blob_and_extracted_metadata(self):
        mappings = validate_procedure_mappings(
            self.parameters,
            [
                {"parameter": "P_ID", "source": "EXCEL", "excel_column": "ID"},
                {
                    "parameter": "P_OPTIONAL",
                    "source": "CONSTANT",
                    "constant": "upload",
                },
                {
                    "parameter": "P_DOCUMENT",
                    "source": "ZIP_BLOB",
                    "zip_folder": "docs",
                    "file_type": "FILE",
                },
                {
                    "parameter": "P_FILENAME",
                    "source": "FILE_NAME",
                    "blob_parameter": "P_DOCUMENT",
                },
            ],
            ["ID", "NOT_A_TARGET_TABLE_COLUMN"],
        )

        self.assertEqual(len(mappings), 4)
        self.assertEqual(mappings[0]["data_type"], "NUMBER")
        self.assertEqual(mappings[2]["zip_folder"], "docs")

    def test_unmapped_inputs_are_optional(self):
        self.assertEqual(
            validate_procedure_mappings(self.parameters, [], ["ID"]),
            [],
        )

    def test_optional_default_parameter_can_be_left_unmapped(self):
        mappings = validate_procedure_mappings(
            self.parameters,
            [
                {"parameter": "P_ID", "source": "EXCEL", "excel_column": "ID"},
                {
                    "parameter": "P_DOCUMENT",
                    "source": "ZIP_BLOB",
                    "zip_folder": "docs",
                    "file_type": "FILE",
                },
                {
                    "parameter": "P_FILENAME",
                    "source": "FILE_NAME",
                    "blob_parameter": "P_DOCUMENT",
                },
                {"parameter": "P_OPTIONAL", "source": ""},
            ],
            ["ID"],
        )

        self.assertNotIn("P_OPTIONAL", {
            mapping["parameter"] for mapping in mappings
        })

        with self.assertRaisesRegex(ValueError, "requires a BLOB parameter"):
            validate_procedure_mappings(
                self.parameters,
                [
                    {
                        "parameter": "P_ID",
                        "source": "ZIP_BLOB",
                        "zip_folder": "docs",
                        "file_type": "FILE",
                    },
                ],
                ["ID"],
            )

    def test_blank_excel_cells_are_null_but_nonempty_values_are_type_checked(self):
        mappings = validate_procedure_mappings(
            self.parameters,
            [
                {"parameter": "P_ID", "source": "EXCEL", "excel_column": "ID"},
                {
                    "parameter": "P_DOCUMENT",
                    "source": "ZIP_BLOB",
                    "zip_folder": "docs",
                    "file_type": "FILE",
                },
                {
                    "parameter": "P_FILENAME",
                    "source": "FILE_NAME",
                    "blob_parameter": "P_DOCUMENT",
                },
            ],
            ["ID"],
        )

        errors = validate_procedure_records(
            [{"ID": ""}, {"ID": "not-a-number"}],
            self.parameters,
            mappings,
        )
        self.assertEqual([error["row"] for error in errors], [3])
        self.assertIn("Invalid NUMBER", errors[0]["error"])

    def test_blank_constants_are_unconfigured_unless_source_is_required(self):
        self.assertEqual(
            validate_procedure_mappings(
                self.parameters,
                [{
                    "parameter": "P_ID",
                    "source": "CONSTANT",
                    "constant": "  ",
                }],
                [],
            ),
            [],
        )

        with self.assertRaisesRegex(
            ValueError,
            "Required constant for parameter P_ID is blank.*Provide a value",
        ):
            validate_procedure_mappings(
                self.parameters,
                [{
                    "parameter": "P_ID",
                    "source": "CONSTANT",
                    "constant": "",
                    "mapping_required": True,
                }],
                [],
            )

    def test_required_mapping_with_no_source_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "requires a mapping source"):
            validate_procedure_mappings(
                self.parameters,
                [{
                    "parameter": "P_ID",
                    "source": "",
                    "mapping_required": True,
                }],
                [],
            )


class ProcedureImportTests(unittest.TestCase):
    def test_blank_excel_value_is_passed_through_as_null(self):
        captured = []

        class Oracle:
            @staticmethod
            def execute_procedure_rows(procedure, rows):
                captured.extend(rows)
                return []

        result = import_procedure_records(
            records=[{"VALUE": ""}],
            zip_path=None,
            mappings=[{
                "parameter": "P_VALUE",
                "source": "EXCEL",
                "excel_column": "VALUE",
            }],
            procedure={
                "parameters": [{
                    "name": "P_VALUE",
                    "data_type": "VARCHAR2",
                }],
            },
            oracle_service=Oracle(),
        )

        self.assertEqual(captured, [(2, {"P_VALUE": None})])
        self.assertEqual(result["success_rows"], 1)

    def test_discovers_standalone_and_packaged_procedures_with_bound_search(self):
        cursor = type("Cursor", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: False,
            "execute": lambda self, statement, **binds: setattr(
                self, "call", (statement, binds)
            ),
            "fetchall": lambda self: [
                ("IMPORT_PKG", "LOAD_ROW", "LOAD_ROW", 2, "1"),
                ("ONE_SHOT", "ONE_SHOT", None, 1, None),
            ],
        })()
        connection = type("Connection", (), {
            "cursor": lambda self: cursor,
            "close": lambda self: None,
        })()
        service = OracleService(type("Config", (), {
            "DB_SCHEMA": "APPS",
        })())
        service.connect = lambda: connection

        result = service.search_procedures("load", page=2)

        statement, binds = cursor.call
        self.assertIn("FROM all_procedures", statement)
        self.assertIn("a.position = 0", statement)
        self.assertEqual(binds["owner"], "APPS")
        self.assertEqual(binds["query"], "LOAD")
        self.assertEqual(binds["offset"], 50)
        self.assertEqual(
            result["procedures"],
            [
                {
                    "package_name": "IMPORT_PKG",
                    "procedure_name": "LOAD_ROW",
                    "subprogram_id": 2,
                    "overload": "1",
                    "label": "IMPORT_PKG.LOAD_ROW (overload 1)",
                },
                {
                    "package_name": None,
                    "procedure_name": "ONE_SHOT",
                    "subprogram_id": 1,
                    "overload": None,
                    "label": "ONE_SHOT",
                },
            ],
        )

    def test_package_parameter_metadata_uses_package_and_subprogram_identity(self):
        class Cursor:
            def __init__(self):
                self.statements = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, statement, **binds):
                self.statements.append((statement, binds))

            def fetchone(self):
                return ("2",)

            def fetchall(self):
                return [
                    (1, "P_ID", "NUMBER", "IN", None, 10, 0, "N", 0, None, None),
                    (2, "P_OUT", "VARCHAR2", "OUT", 100, None, None, "N", 0, None, None),
                    (3, "P_DEFAULT", "VARCHAR2", "IN", 100, None, None, "Y", 0, None, None),
                ]

        class Connection:
            def __init__(self):
                self.fake_cursor = Cursor()

            def cursor(self):
                return self.fake_cursor

            def close(self):
                pass

        service = OracleService(type("Config", (), {
            "DB_SCHEMA": "APPS",
        })())
        connection = Connection()
        service.connect = lambda: connection

        metadata = service.get_procedure_metadata("IMPORT_PKG", "LOAD_ROW", 7)

        self.assertEqual(metadata["overload"], "2")
        self.assertEqual(metadata["parameters"][0]["name"], "P_ID")
        self.assertIsNone(metadata["parameters"][0]["nullable"])
        self.assertFalse(metadata["parameters"][0]["defaulted"])
        self.assertTrue(metadata["parameters"][2]["defaulted"])
        self.assertEqual(connection.call_timeout, 30_000)
        argument_query, argument_binds = connection.fake_cursor.statements[1]
        self.assertIn("object_name = :procedure_name", argument_query)
        self.assertEqual(argument_binds["procedure_name"], "LOAD_ROW")
        self.assertEqual(argument_binds["package_name"], "IMPORT_PKG")

    def test_reads_zip_blob_and_metadata_for_procedure_binds(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_path = Path(temp_dir) / "docs.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr("docs/a.pdf", b"pdf-data")

            captured = []

            class Oracle:
                @staticmethod
                def execute_procedure_rows(procedure, rows):
                    captured.extend(rows)
                    return []

            procedure = {
                "parameters": [
                    {"name": "P_ID", "data_type": "NUMBER"},
                    {"name": "P_DOCUMENT", "data_type": "BLOB"},
                    {"name": "P_FILENAME", "data_type": "VARCHAR2"},
                    {"name": "P_SIZE", "data_type": "NUMBER"},
                ],
            }
            mappings = [
                {
                    "parameter": "P_ID",
                    "source": "EXCEL",
                    "excel_column": "ID",
                },
                {"parameter": "P_DOCUMENT", "source": "ZIP_BLOB"},
                {
                    "parameter": "P_FILENAME",
                    "source": "FILE_NAME",
                    "blob_parameter": "P_DOCUMENT",
                },
                {
                    "parameter": "P_SIZE",
                    "source": "FILE_SIZE",
                    "blob_parameter": "P_DOCUMENT",
                },
            ]
            result = import_procedure_records(
                records=[{"ID": "42"}],
                zip_path=zip_path,
                mappings=mappings,
                procedure=procedure,
                oracle_service=Oracle(),
                assignment_map={"P_DOCUMENT": {2: "docs/a.pdf"}},
                assignment_metadata={
                    "P_DOCUMENT": {
                        2: {
                            "filename": "a.pdf",
                            "size": 8,
                        },
                    },
                },
            )

        self.assertEqual(
            captured,
            [(
                2,
                {
                    "P_ID": Decimal("42"),
                    "P_DOCUMENT": b"pdf-data",
                    "P_FILENAME": "a.pdf",
                    "P_SIZE": Decimal("8"),
                },
            )],
        )
        self.assertEqual(result["success_rows"], 1)
        self.assertEqual(result["success_files"], 1)

    def test_executes_using_callproc_and_rolls_back_only_failed_row(self):
        class Variable:
            def __init__(self):
                self.value = None

            def setvalue(self, position, value):
                self.value = value

        class Cursor:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def var(self, *args, **kwargs):
                return Variable()

            def callproc(self, name, keyword_parameters):
                self.calls.append((name, keyword_parameters))
                if keyword_parameters["P_ID"].value == Decimal("2"):
                    raise RuntimeError("ORA-20001: procedure rejected row")

        class Connection:
            def __init__(self):
                self.fake_cursor = Cursor()
                self.commits = 0
                self.rollbacks = 0

            def cursor(self):
                return self.fake_cursor

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

            def close(self):
                pass

        service = OracleService(type("Config", (), {
            "DB_SCHEMA": "APPS",
        })())
        connection = Connection()
        service.connect = lambda: connection
        errors = service.execute_procedure_rows(
            {
                "package_name": "IMPORT_PKG",
                "procedure_name": "LOAD_ROW",
                "parameters": [
                    {
                        "name": "P_ID",
                        "data_type": "NUMBER",
                        "mode": "IN",
                        "data_level": 0,
                    },
                    {
                        "name": "P_RESULT",
                        "data_type": "VARCHAR2",
                        "mode": "OUT",
                        "data_length": 100,
                        "data_level": 0,
                    },
                ],
            },
            [
                (2, {"P_ID": Decimal("1")}),
                (3, {"P_ID": Decimal("2")}),
                (4, {"P_ID": Decimal("3")}),
            ],
        )

        self.assertEqual(connection.commits, 2)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(errors, [{
            "row": 3,
            "error": "ORA-20001: procedure rejected row",
        }])
        self.assertTrue(all(
            call[0] == "APPS.IMPORT_PKG.LOAD_ROW"
            for call in connection.fake_cursor.calls
        ))
        self.assertEqual(
            connection.fake_cursor.calls[0][1]["P_ID"].value,
            Decimal("1"),
        )

    def test_defaulted_blank_is_omitted_while_unknown_nullable_and_out_are_bound(self):
        class Variable:
            def __init__(self):
                self.value = None
                self.set_values = []

            def setvalue(self, position, value):
                self.value = value
                self.set_values.append((position, value))

        class Cursor:
            def __init__(self):
                self.variables = []
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def var(self, *args, **kwargs):
                variable = Variable()
                self.variables.append(variable)
                return variable

            def callproc(self, name, keyword_parameters):
                self.calls.append((name, keyword_parameters))

        class Connection:
            def __init__(self):
                self.fake_cursor = Cursor()
                self.commits = 0
                self.rollbacks = 0

            def cursor(self):
                return self.fake_cursor

            def commit(self):
                self.commits += 1

            def rollback(self):
                self.rollbacks += 1

            def close(self):
                pass

        service = OracleService(type("Config", (), {
            "DB_SCHEMA": "APPS",
        })())
        connection = Connection()
        service.connect = lambda: connection
        errors = service.execute_procedure_rows(
            {
                "procedure_name": "LOAD_ROW",
                "parameters": [
                    {
                        "name": "P_NULLABLE",
                        "data_type": "VARCHAR2",
                        "mode": "IN",
                        "defaulted": False,
                        "nullable": None,
                        "data_length": 100,
                    },
                    {
                        "name": "P_DEFAULTED",
                        "data_type": "VARCHAR2",
                        "mode": "IN",
                        "defaulted": True,
                        "data_length": 100,
                    },
                    {
                        "name": "P_RESULT",
                        "data_type": "VARCHAR2",
                        "mode": "OUT",
                        "defaulted": False,
                        "data_length": 100,
                    },
                ],
            },
            [(2, {"P_NULLABLE": None, "P_DEFAULTED": None})],
        )

        self.assertEqual(errors, [])
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        keyword_parameters = connection.fake_cursor.calls[0][1]
        self.assertEqual(
            set(keyword_parameters),
            {"P_NULLABLE", "P_RESULT"},
        )
        self.assertIsNone(keyword_parameters["P_NULLABLE"].value)
        self.assertEqual(keyword_parameters["P_NULLABLE"].set_values, [(0, None)])
        self.assertEqual(keyword_parameters["P_RESULT"].set_values, [])


if __name__ == "__main__":
    unittest.main()
