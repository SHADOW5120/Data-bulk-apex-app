import os
import unittest
from unittest.mock import patch

os.environ["FLASK_SECRET_KEY"] = "procedure-route-test-key"

import config

config.Config.SECRET_KEY = os.environ["FLASK_SECRET_KEY"]

import app as app_module


class ProcedureMappingRouteTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.import_id = "a" * 32
        self.metadata = {
            "schema": "APPS",
            "package_name": "IMPORT_PKG",
            "procedure_name": "LOAD_ROW",
            "subprogram_id": 4,
            "overload": "2",
            "parameters": [{
                "position": 1,
                "name": "P_ID",
                "data_type": "NUMBER",
                "mode": "IN",
                "data_length": None,
                "data_precision": 10,
                "data_scale": 0,
                "defaulted": False,
                "nullable": None,
                "data_level": 0,
                "type_owner": None,
                "type_name": None,
            }],
        }
        self.job = {
            "import_id": self.import_id,
            "import_mode": "DIRECT",
            "selected_sheet": "Sheet1",
            "procedure": None,
            "procedure_metadata": None,
            "mappings": [],
        }
        self.client = app_module.app.test_client()

    def _request_patches(self, procedure_metadata):
        metadata_patch = (
            patch.object(
                app_module.oracle,
                "get_procedure_metadata",
                side_effect=procedure_metadata,
            )
            if isinstance(procedure_metadata, BaseException)
            else patch.object(
                app_module.oracle,
                "get_procedure_metadata",
                return_value=procedure_metadata,
            )
        )
        return (
            patch.object(
                app_module,
                "get_job",
                return_value=self.job,
            ),
            patch.object(
                app_module,
                "get_job_files",
                return_value=(None, None),
            ),
            metadata_patch,
            patch.object(app_module, "save_job"),
            patch.object(app_module.oracle, "search_tables"),
            patch.object(app_module.oracle, "search_procedures"),
        )

    def test_ajax_load_returns_json_and_next_get_renders_parameters_without_searches(self):
        patches = self._request_patches(self.metadata)
        with patches[0], patches[1], patches[2] as get_metadata, patches[3], \
                patches[4] as search_tables, patches[5] as search_procedures:
            response = self.client.post(
                f"/mapping/{self.import_id}",
                data={
                    "import_mode": "PROCEDURE",
                    "procedure_key": "IMPORT_PKG|LOAD_ROW|4",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/json")
            self.assertTrue(response.json["success"])
            get_metadata.assert_called_once_with("IMPORT_PKG", "LOAD_ROW", "4")
            search_tables.assert_not_called()
            search_procedures.assert_not_called()

            with patch.object(
                app_module,
                "read_excel_data",
                return_value={"columns": ["ID"]},
            ), patch.object(
                app_module,
                "get_zip_information",
                return_value={"files": [], "folders": []},
            ):
                page = self.client.get(response.json["redirect_url"])

            self.assertEqual(page.status_code, 200)
            self.assertIn(b"Procedure Parameter Mapping", page.data)
            self.assertIn(b"P_ID", page.data)
            self.assertIn(b"parameter_requirement", page.data)
            search_tables.assert_not_called()
            search_procedures.assert_not_called()

    def test_ajax_load_returns_an_error_response_for_metadata_failures(self):
        patches = self._request_patches(
            RuntimeError("Oracle metadata lookup failed")
        )
        with patches[0], patches[1], patches[2], patches[3], \
                patches[4] as search_tables, patches[5] as search_procedures, \
                patch.object(app_module.app.logger, "exception"):
            response = self.client.post(
                f"/mapping/{self.import_id}",
                data={
                    "import_mode": "PROCEDURE",
                    "procedure_key": "IMPORT_PKG|LOAD_ROW|4",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.mimetype, "application/json")
        self.assertFalse(response.json["success"])
        self.assertIn("Oracle metadata lookup failed", response.json["error"])
        search_tables.assert_not_called()
        search_procedures.assert_not_called()

    def test_required_source_allows_blank_excel_cell_to_reach_procedure(self):
        self.job["import_mode"] = "PROCEDURE"
        self.job["procedure"] = {
            "package_name": "IMPORT_PKG",
            "procedure_name": "LOAD_ROW",
            "subprogram_id": 4,
        }
        with patch.object(app_module, "get_job", return_value=self.job), \
                patch.object(app_module, "get_job_files", return_value=(None, None)), \
                patch.object(
                    app_module,
                    "_refresh_procedure_metadata",
                    return_value=self.metadata,
                ), patch.object(
                    app_module,
                    "read_excel_data",
                    return_value={"columns": ["ID"]},
                ), patch.object(
                    app_module,
                    "read_excel_records",
                    return_value=[{"ID": ""}],
                ), patch.object(
                    app_module,
                    "get_zip_information",
                    return_value={"files": [], "folders": []},
                ), patch.object(
                    app_module,
                    "review_zip_folders",
                    return_value={"errors": []},
                ), patch.object(app_module, "save_job"):
            response = self.client.post(
                f"/review/{self.import_id}",
                data={
                    "procedure_parameter": ["P_ID"],
                    "parameter_source": ["EXCEL"],
                    "parameter_requirement": ["REQUIRED"],
                    "parameter_excel_column": ["ID"],
                    "parameter_constant": [""],
                    "parameter_zip_folder": [""],
                    "parameter_file_type": ["FILE"],
                    "parameter_blob_parameter": [""],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.job["status"], "REVIEWED")
        self.assertTrue(self.job["mappings"][0]["mapping_required"])


if __name__ == "__main__":
    unittest.main()
