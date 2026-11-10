import unittest
from types import SimpleNamespace

from app.nosql_store import NoSQLConflictError, NoSQLStore


class NoSQLStoreTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.version = "v1"
        self.client = SimpleNamespace(get_row=self.get_row, update_row=self.update_row)
        self.store = NoSQLStore(self.client, lambda **kw: SimpleNamespace(**kw), "compartment", "jit_test")

    def get_row(self, table, keys, **kwargs):
        self.calls.append((table, keys, kwargs))
        return SimpleNamespace(data=SimpleNamespace(value={"request_id": "r1"}), headers={"etag": "v1"})

    def update_row(self, table, details, **kwargs):
        self.calls.append((table, details, kwargs))
        return SimpleNamespace(data=SimpleNamespace(version=self.version), headers={"etag": "etag-new"})

    def test_read_uses_compartment_and_absolute_consistency(self):
        value, version = self.store.get("requests", "r1")
        self.assertEqual((value, version), ({"request_id": "r1"}, "v1"))
        self.assertEqual(self.calls[0], ("jit_test_requests", ["request_id:r1"], {"compartment_id": "compartment", "consistency": "ABSOLUTE"}))

    def test_create_requires_absent_row(self):
        self.assertEqual(self.store.create("requests", {"request_id": "r1"}), "etag-new")
        self.assertEqual(self.calls[0][1].option, "IF_ABSENT")
        self.version = None
        with self.assertRaises(NoSQLConflictError):
            self.store.create("requests", {"request_id": "r1"})

    def test_replace_requires_version(self):
        with self.assertRaises(ValueError):
            self.store.replace("requests", {"request_id": "r1"}, None)
        self.assertEqual(self.store.replace("requests", {"request_id": "r1"}, "v1"), "etag-new")
        self.assertEqual(self.calls[0][2]["if_match"], "v1")

    def test_unknown_table_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.get("other", "r1")


if __name__ == "__main__":
    unittest.main()
