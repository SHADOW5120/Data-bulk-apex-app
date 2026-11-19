import tempfile
import unittest
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from openpyxl import Workbook

from services.excel_service import read_excel_records
from services.file_service import (
    inspect_zip,
    review_zip_folders,
)
from services.import_service import import_records
from services.mapping_service import build_mapping
from services.oracle_service import OracleService
from services.validation_service import (
    validate_file_metadata,
    validate_records,
    validate_value,
)


class ValidationAndDateTests(unittest.TestCase):
    def test_existing_update_needs_only_mapped_primary_key_and_blob(self):
        metadata = {"columns": [
            {"column_name": "ID", "data_type": "NUMBER", "nullable": "N"},
            {"column_name": "PHOTO", "data_type": "BLOB", "nullable": "N"},
            {"column_name": "SOB_ID", "data_type": "NUMBER", "nullable": "N"},
            {"column_name": "ORG_ID", "data_type": "NUMBER", "nullable": "N"},
            {"column_name": "CREATION_DATE", "data_type": "DATE", "nullable": "N"},
            {"column_name": "CREATED_BY", "data_type": "NUMBER", "nullable": "N"},
        ]}
        mappings = [
            {
                "excel_column": "ID",
                "oracle_column": "ID",
                "column_type": "DATA",
            },
            {
                "excel_column": "",
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
            },
        ]

        errors = validate_records(
            [{"ID": 42}],
            metadata,
            mappings,
            {},
            row_operations={2: "UPDATE"},
        )

        self.assertEqual(errors, [])

    def test_insert_still_requires_unmapped_not_null_columns(self):
        metadata = {"columns": [
            {"column_name": "ID", "data_type": "NUMBER", "nullable": "N"},
            {"column_name": "PHOTO", "data_type": "BLOB", "nullable": "N"},
            {"column_name": "SOB_ID", "data_type": "NUMBER", "nullable": "N"},
            {"column_name": "ORG_ID", "data_type": "NUMBER", "nullable": "N"},
        ]}
        mappings = [
            {
                "excel_column": "ID",
                "oracle_column": "ID",
                "column_type": "DATA",
            },
            {
                "excel_column": "",
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
            },
        ]

        errors = validate_records(
            [{"ID": 42}],
            metadata,
            mappings,
            {},
            row_operations={2: "INSERT"},
        )

        self.assertEqual(
            {error["column"] for error in errors},
            {"SOB_ID", "ORG_ID"},
        )
        self.assertTrue(all(error["operation"] == "INSERT" for error in errors))

    def test_excel_date_serial_and_native_dates_convert_to_datetime(self):
        expected = datetime(2026, 11, 18)
        self.assertEqual(validate_value(46344, "DATE"), expected)
        self.assertEqual(validate_value(46344, "TIMESTAMP(6)"), expected)
        self.assertEqual(validate_value(expected, "DATE"), expected)
        self.assertEqual(
            validate_value(date(2026, 11, 18), "DATE"),
            expected,
        )
        self.assertIsNone(validate_value(None, "DATE"))
        self.assertEqual(
            validate_value(46344, "NUMBER"),
            Decimal("46344"),
        )

    def test_import_passes_converted_dates_and_numbers_to_oracle(self):
        oracle = MagicMock()
        captured_rows = []

        def capture_rows(table, columns, rows, **kwargs):
            captured_rows.extend(rows)
            return []

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        workbook_path = Path(temp_dir.name) / "dates.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        sheet.append(["CREATION_DATE", "UPDATED_DATE", "SOB_ID"])
        sheet.append([
            46344,
            datetime(2026, 11, 18),
            46344,
        ])
        workbook.save(workbook_path)
        records = read_excel_records(workbook_path, "Data")

        oracle.insert_many.side_effect = capture_rows
        result = import_records(
            records=records,
            zip_path=None,
            file_index={},
            table_name="TARGET",
            metadata={"columns": [
                {"column_name": "CREATION_DATE", "data_type": "DATE"},
                {"column_name": "UPDATED_DATE", "data_type": "DATE"},
                {"column_name": "SOB_ID", "data_type": "NUMBER"},
            ]},
            mappings=[
                {
                    "excel_column": "CREATION_DATE",
                    "oracle_column": "CREATION_DATE",
                    "column_type": "DATA",
                },
                {
                    "excel_column": "UPDATED_DATE",
                    "oracle_column": "UPDATED_DATE",
                    "column_type": "DATA",
                },
                {
                    "excel_column": "SOB_ID",
                    "oracle_column": "SOB_ID",
                    "column_type": "DATA",
                },
            ],
            oracle_service=oracle,
        )

        self.assertEqual(
            captured_rows[0][1],
            (
                datetime(2026, 11, 18),
                datetime(2026, 11, 18),
                Decimal("46344"),
            ),
        )
        self.assertEqual(result["success_rows"], 1)


class ZipFolderMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.zip_path = Path(self.temp_dir.name) / "files.zip"
        with zipfile.ZipFile(self.zip_path, "w") as archive:
            archive.writestr("images/photo10.jpg", b"ten")
            archive.writestr("images/photo2.jpg", b"two")
            archive.writestr("images/photo1.jpg", b"one")
            archive.writestr("empty/", b"")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_natural_order_assigns_files_to_rows(self):
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
            }],
            3,
        )

        self.assertEqual(
            [item["file"] for item in review["folders"][0]["assignments"]],
            ["images/photo1.jpg", "images/photo2.jpg", "images/photo10.jpg"],
        )
        self.assertEqual(review["errors"], [])
        self.assertEqual(
            list(review["assignment_map"]["PHOTO"].values()),
            ["images/photo1.jpg", "images/photo2.jpg", "images/photo10.jpg"],
        )
        self.assertEqual(
            review["folders"][0]["assignments"][0]["metadata"],
            {
                "filename": "photo1.jpg",
                "extension": ".jpg",
                "size": 3,
                "mime_type": "image/jpeg",
            },
        )

    def test_file_count_and_file_type_errors_are_reported(self):
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "DOC",
                "column_type": "FILE",
                "zip_folder": "images",
            }],
            2,
        )

        folder = review["folders"][0]
        self.assertEqual(folder["actual_count"], 3)
        self.assertEqual(len(folder["extra_files"]), 1)
        self.assertEqual(len(folder["invalid_files"]), 3)
        self.assertEqual(len(review["errors"]), 2)

    def test_missing_rows_are_reported(self):
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
            }],
            4,
        )

        self.assertEqual(review["folders"][0]["missing_rows"], [5])
        self.assertEqual(len(review["errors"]), 1)

    def test_explicit_empty_zip_folder_is_available_for_review(self):
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "empty",
            }],
            2,
            info["folders"],
        )

        self.assertEqual(review["folders"][0]["actual_count"], 0)
        self.assertEqual(review["folders"][0]["missing_rows"], [2, 3])

    def test_folder_includes_subfolders_and_overlaps_are_rejected(self):
        with zipfile.ZipFile(self.zip_path, "a") as archive:
            archive.writestr("images/sub/photo3.jpg", b"three")
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
            }],
            4,
        )
        self.assertEqual(
            [item["file"] for item in review["folders"][0]["assignments"]],
            [
                "images/photo1.jpg",
                "images/photo2.jpg",
                "images/sub/photo3.jpg",
                "images/photo10.jpg",
            ],
        )

        overlapping_review = review_zip_folders(
            info["files"],
            [
                {
                    "oracle_column": "PHOTO",
                    "column_type": "IMAGE",
                    "zip_folder": "images",
                },
                {
                    "oracle_column": "THUMBNAIL",
                    "column_type": "IMAGE",
                    "zip_folder": "images/sub",
                },
            ],
            4,
        )
        self.assertEqual(len(overlapping_review["folders"]), 1)
        self.assertIn("overlaps", overlapping_review["errors"][0]["error"])

    def test_folder_mapping_is_supported_without_excel_path(self):
        mappings = build_mapping(
            ["Name"],
            {
                "columns": [
                    {"column_name": "NAME", "data_type": "VARCHAR2"},
                    {"column_name": "PHOTO", "data_type": "BLOB"},
                    {"column_name": "ORIGINAL_NAME", "data_type": "VARCHAR2"},
                    {"column_name": "FILE_EXT", "data_type": "VARCHAR2"},
                    {"column_name": "FILE_BYTES", "data_type": "NUMBER"},
                    {"column_name": "MIME", "data_type": "VARCHAR2"},
                ]
            },
            [{
                "excel_column": "",
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
                "metadata_columns": {
                    "filename": "ORIGINAL_NAME",
                    "extension": "FILE_EXT",
                    "size": "FILE_BYTES",
                    "mime_type": "MIME",
                },
            }],
        )
        self.assertEqual(mappings[0]["excel_column"], "")
        self.assertEqual(mappings[0]["zip_folder"], "images")
        self.assertEqual(
            mappings[0]["metadata_columns"],
            {
                "filename": "ORIGINAL_NAME",
                "extension": "FILE_EXT",
                "size": "FILE_BYTES",
                "mime_type": "MIME",
            },
        )

    def test_metadata_validation_checks_oracle_column_sizes(self):
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
                "metadata_columns": {"filename": "SHORT_NAME"},
            }],
            3,
        )
        errors = validate_file_metadata(
            review,
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "metadata_columns": {"filename": "SHORT_NAME"},
            }],
            {"columns": [{
                "column_name": "SHORT_NAME",
                "data_type": "VARCHAR2",
                "data_length": 5,
            }]},
        )
        self.assertEqual(len(errors), 3)

    def test_metadata_number_validation_honors_precision_and_scale(self):
        info = inspect_zip(self.zip_path, _ZipConfig)
        review = review_zip_folders(
            info["files"],
            [{
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
                "metadata_columns": {"size": "FILE_SIZE"},
            }],
            3,
        )
        assignments = review["folders"][0]["assignments"]
        assignments[0]["metadata"]["size"] = 9999
        mapping = [{
            "oracle_column": "PHOTO",
            "column_type": "IMAGE",
            "metadata_columns": {"size": "FILE_SIZE"},
        }]

        errors = validate_file_metadata(
            review,
            mapping,
            {"columns": [{
                "column_name": "FILE_SIZE",
                "data_type": "NUMBER",
                "data_precision": 2,
                "data_scale": -2,
            }]},
        )
        self.assertEqual(len(errors), 1)

        for assignment in assignments:
            assignment["metadata"]["size"] = 0
        errors = validate_file_metadata(
            review,
            mapping,
            {"columns": [{
                "column_name": "FILE_SIZE",
                "data_type": "NUMBER",
                "data_precision": 3,
                "data_scale": 2,
            }]},
        )
        self.assertEqual(errors, [])

    def test_import_reads_assigned_zip_members_for_rows(self):
        oracle = MagicMock()
        captured_rows = []

        def capture_rows(**kwargs):
            rows = kwargs["rows"]
            captured_rows.extend(rows)
            return {
                "errors": [],
                "statuses": {},
                "inserted_rows": 3,
                "updated_files": 0,
                "replaced_files": 0,
                "skipped_files": 0,
            }

        oracle.merge_blob_records.side_effect = capture_rows
        mappings = [
            {
                "excel_column": "NAME",
                "oracle_column": "NAME",
                "column_type": "DATA",
            },
            {
                "excel_column": "",
                "oracle_column": "PHOTO",
                "column_type": "IMAGE",
                "zip_folder": "images",
            },
        ]
        result = import_records(
            records=[{"NAME": "first"}, {"NAME": "second"}, {"NAME": "third"}],
            zip_path=self.zip_path,
            file_index={},
            table_name="TARGET",
            metadata={
                "columns": [
                    {"column_name": "NAME", "data_type": "VARCHAR2"},
                    {"column_name": "PHOTO", "data_type": "BLOB"},
                ]
            },
            mappings=mappings,
            oracle_service=oracle,
            assignment_map={
                "PHOTO": {
                    2: "images/photo1.jpg",
                    3: "images/photo2.jpg",
                    4: "images/photo10.jpg",
                }
            },
            assignment_metadata={
                "PHOTO": {
                    2: {"filename": "photo1.jpg", "extension": ".jpg", "size": 3, "mime_type": "image/jpeg"},
                    3: {"filename": "photo2.jpg", "extension": ".jpg", "size": 3, "mime_type": "image/jpeg"},
                    4: {"filename": "photo10.jpg", "extension": ".jpg", "size": 3, "mime_type": "image/jpeg"},
                }
            },
            primary_keys=["NAME"],
            key_rows=[(2, ("first",)), (3, ("second",)), (4, ("third",))],
            expected_blob_states={"2": None, "3": None, "4": None},
        )

        self.assertEqual(
            [
                values["PHOTO"]["content"]
                for _, _, values in captured_rows
            ],
            [
                b"one",
                b"two",
                b"ten",
            ],
        )
        self.assertEqual(result["success_rows"], 3)

    def test_excel_only_import_does_not_require_a_zip(self):
        oracle = MagicMock()

        def capture_rows(table, columns, rows, **kwargs):
            list(rows)
            return []

        oracle.insert_many.side_effect = capture_rows
        result = import_records(
            records=[{"NAME": "only data"}],
            zip_path=None,
            file_index={},
            table_name="TARGET",
            metadata={"columns": [{"column_name": "NAME", "data_type": "VARCHAR2"}]},
            mappings=[{
                "excel_column": "NAME",
                "oracle_column": "NAME",
                "column_type": "DATA",
            }],
            oracle_service=oracle,
        )

        self.assertEqual(result["success_rows"], 1)


class OracleTableSearchTests(unittest.TestCase):
    def test_search_is_applied_in_apps_and_limited_to_one_page(self):
        config = type("Config", (), {
            "DB_SCHEMA": "APPS",
            "DB_USER": "APPUSER",
            "DB_HOST": "localhost",
            "DB_SERVICE": "ORCL",
            "DB_PASSWORD": "password",
            "DB_PORT": 1521,
            "DB_CONNECT_TIMEOUT": 10,
        })
        service = OracleService(config)
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [(f"TABLE_{i}",) for i in range(51)]
        service.connect = lambda: connection

        result = service.search_tables("table", page=2)

        self.assertEqual(len(result["tables"]), 50)
        self.assertTrue(result["has_more"])
        args, kwargs = cursor.execute.call_args
        self.assertIn("owner = :owner", args[0])
        self.assertIn("INSTR(UPPER(table_name), :query)", args[0])
        self.assertEqual(kwargs["owner"], "APPS")
        self.assertEqual(kwargs["offset"], 50)
        self.assertEqual(kwargs["fetch_count"], 51)


class OracleBlobMergeTests(unittest.TestCase):
    def setUp(self):
        self.config = type("Config", (), {
            "DB_SCHEMA": "APPS",
            "DB_USER": "APPUSER",
            "DB_HOST": "localhost",
            "DB_SERVICE": "ORCL",
            "DB_PASSWORD": "password",
            "DB_PORT": 1521,
            "DB_CONNECT_TIMEOUT": 10,
            "IMPORT_BATCH_SIZE": 50,
            "MAX_BLOB_BATCH_BYTES": 1024,
        })
        self.service = OracleService(self.config)
        self.cursor = MagicMock()
        self.connection = MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor
        self.service.connect = lambda: self.connection
        self.types = {
            "ID": "NUMBER",
            "NAME": "VARCHAR2",
            "PHOTO": "BLOB",
            "ORIGINAL_NAME": "VARCHAR2",
            "SOB_ID": "NUMBER",
        }
        self.blob_mappings = [{
            "oracle_column": "PHOTO",
            "column_type": "IMAGE",
            "metadata_columns": {"filename": "ORIGINAL_NAME"},
        }]

    def test_replace_updates_only_blob_and_mapped_metadata(self):
        self.cursor.fetchall.return_value = [(7, 0)]
        result = self.service.merge_blob_records(
            table_name="TARGET",
            primary_keys=["ID"],
            key_rows=[(2, (7,))],
            blob_mappings=self.blob_mappings,
            column_types=self.types,
            expected_states={"2": {"PHOTO": False}},
            rows=[(
                2,
                {"ID": 7},
                {"PHOTO": {
                    "action": "REPLACE",
                    "content": b"replacement",
                    "metadata": {"ORIGINAL_NAME": "new.jpg"},
                }},
            )],
        )

        statement = self.cursor.executemany.call_args.args[0]
        self.assertIn('SET "PHOTO" = :1, "ORIGINAL_NAME" = :2', statement)
        self.assertNotIn('"NAME" =', statement)
        self.assertNotIn('"SOB_ID" =', statement)
        self.assertEqual(result["replaced_files"], 1)
        self.connection.commit.assert_called_once()

    def test_skip_does_not_execute_an_update(self):
        self.cursor.fetchall.return_value = [(7, 0)]
        result = self.service.merge_blob_records(
            table_name="TARGET",
            primary_keys=["ID"],
            key_rows=[(2, (7,))],
            blob_mappings=self.blob_mappings,
            column_types=self.types,
            expected_states={"2": {"PHOTO": False}},
            rows=[(
                2,
                {"ID": 7},
                {"PHOTO": {"action": "SKIP", "metadata": {}}},
            )],
        )

        self.cursor.executemany.assert_not_called()
        self.assertEqual(result["skipped_files"], 1)
        self.connection.commit.assert_called_once()

    def test_existing_null_blob_updates_explicit_data_columns(self):
        self.cursor.fetchall.return_value = [(7, 1)]
        result = self.service.merge_blob_records(
            table_name="TARGET",
            primary_keys=["ID"],
            key_rows=[(2, (7,))],
            blob_mappings=self.blob_mappings,
            column_types=self.types,
            expected_states={"2": {"PHOTO": True}},
            rows=[(
                2,
                {"ID": 7, "NAME": "unchanged"},
                {"PHOTO": {
                    "action": "SKIP",
                    "content": b"new-content",
                    "metadata": {"ORIGINAL_NAME": "new.jpg"},
                }},
            )],
        )

        statement = self.cursor.executemany.call_args.args[0]
        self.assertIn("UPDATE", statement)
        self.assertIn('"NAME" =', statement)
        self.assertNotIn('"SOB_ID" =', statement)
        self.assertEqual(result["updated_files"], 1)
        self.assertEqual(result["statuses"][2]["PHOTO"], "UPDATE")

    def test_existing_null_blob_updates_with_only_primary_key_and_blob(self):
        self.cursor.fetchall.return_value = [(7, 1)]
        result = self.service.merge_blob_records(
            table_name="TARGET",
            primary_keys=["ID"],
            key_rows=[(2, (7,))],
            blob_mappings=self.blob_mappings,
            column_types=self.types,
            expected_states={"2": {"PHOTO": True}},
            rows=[(
                2,
                {"ID": 7},
                {"PHOTO": {
                    "action": "SKIP",
                    "content": b"new-content",
                    "metadata": {"ORIGINAL_NAME": "new.jpg"},
                }},
            )],
        )

        statement = self.cursor.executemany.call_args.args[0]
        self.assertIn('SET "PHOTO" = :1, "ORIGINAL_NAME" = :2', statement)
        self.assertNotIn('"SOB_ID" =', statement)
        self.assertEqual(result["updated_files"], 1)

    def test_datetime_is_bound_directly_as_oracle_date(self):
        value = datetime(2026, 11, 18)
        self.service.insert_many(
            table_name="TARGET",
            column_names=["CREATION_DATE"],
            rows=iter([(2, (value,))]),
            column_types={"CREATION_DATE": "DATE"},
        )

        self.cursor.setinputsizes.assert_called_once_with(
            self.service._bind_type("DATE")
        )
        self.assertEqual(
            self.cursor.executemany.call_args.args[1],
            [(value,)],
        )

    def test_new_row_inserts_excel_data_blob_and_metadata(self):
        self.cursor.fetchall.return_value = []
        result = self.service.merge_blob_records(
            table_name="TARGET",
            primary_keys=["ID"],
            key_rows=[(2, (7,))],
            blob_mappings=self.blob_mappings,
            column_types=self.types,
            expected_states={"2": None},
            rows=[(
                2,
                {"ID": 7, "NAME": "new row"},
                {"PHOTO": {
                    "action": "SKIP",
                    "content": b"new-content",
                    "metadata": {"ORIGINAL_NAME": "new.jpg"},
                }},
            )],
        )

        statement = self.cursor.executemany.call_args.args[0]
        self.assertIn("INSERT INTO", statement)
        self.assertIn('"NAME"', statement)
        self.assertIn('"PHOTO"', statement)
        self.assertIn('"ORIGINAL_NAME"', statement)
        self.assertEqual(result["inserted_rows"], 1)

    def test_state_change_after_review_rolls_back_before_dml(self):
        self.cursor.fetchall.return_value = [(7, 0)]
        with self.assertRaisesRegex(RuntimeError, "changed after review"):
            self.service.merge_blob_records(
                table_name="TARGET",
                primary_keys=["ID"],
                key_rows=[(2, (7,))],
                blob_mappings=self.blob_mappings,
                column_types=self.types,
                expected_states={"2": None},
                rows=[],
            )

        self.cursor.executemany.assert_not_called()
        self.connection.rollback.assert_called_once()


class _ZipConfig:
    MAX_SINGLE_FILE_SIZE = 50 * 1024 * 1024
    MAX_TOTAL_UNCOMPRESSED_SIZE = 2 * 1024 * 1024 * 1024
    MAX_COMPRESSION_RATIO = 200
    MAX_ZIP_FILES = 5000


if __name__ == "__main__":
    unittest.main()
