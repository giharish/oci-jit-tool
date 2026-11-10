import contextlib
import importlib.util
import io
import unittest
from pathlib import Path


path = Path(__file__).resolve().parents[1] / "deployment" / "provision_oci_nosql.py"
spec = importlib.util.spec_from_file_location("provision_oci_nosql", path)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class NoSQLProvisioningTest(unittest.TestCase):
    def setUp(self):
        self.args = ["--compartment-id", "ocid1.compartment.oc1..test", "--region", "ap-hyderabad-1"]
        self.calls = []

    def call(self, args):
        self.calls.append(args)
        return {"data": {"items": []}}

    def test_dry_run_does_not_call_oci(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(provision.main(self.args, self.call), 0)
        self.assertEqual(self.calls, [])

    def test_existing_table_stops_before_creation(self):
        def existing(args):
            self.calls.append(args)
            return {"data": {"items": [{"name": "jit_requests"}]}}

        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Existing table names"):
                provision.main(self.args + ["--apply"], existing)
        self.assertEqual(len(self.calls), 1)

    def test_unexpected_list_response_fails_closed(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "Unexpected table-list response"):
                provision.main(self.args + ["--apply"], lambda _: {})

    def test_apply_creates_only_planned_tables_and_indexes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(provision.main(self.args + ["--apply"], self.call), 0)
        creates = [call for call in self.calls if call[:3] == ["nosql", "table", "create"]]
        indexes = [call for call in self.calls if call[:3] == ["nosql", "index", "create"]]
        self.assertEqual(len(creates), 5)
        self.assertEqual(len(indexes), 5)
        self.assertTrue(all("--wait-for-state" in call for call in creates + indexes))


if __name__ == "__main__":
    unittest.main()
