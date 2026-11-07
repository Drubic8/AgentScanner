"""Credential persistence uses mock protection except one local Windows roundtrip."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_ui.ai_credentials import AiCredentialStore, MAGIC, MAX_FILE_BYTES


def mock_crypt(data, *, decrypt=False):
    if decrypt:
        if not data.startswith(b"TEST-CIPHERTEXT:"):
            raise ValueError("synthetic-key must not appear in an error")
        return bytes(value ^ 173 for value in data[len(b"TEST-CIPHERTEXT:"):])
    return b"TEST-CIPHERTEXT:" + bytes(value ^ 173 for value in data)


class AiCredentialTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / "settings" / "ai_credentials.dat"
        self.store = AiCredentialStore(self.path, crypt=mock_crypt)

    def test_missing_roundtrip_and_delete_never_leave_plaintext(self):
        self.assertEqual(self.store.load(), "")
        self.store.save("synthetic-key")
        raw = self.path.read_bytes()
        self.assertTrue(raw.startswith(MAGIC))
        self.assertNotIn(b"synthetic-key", raw)
        self.assertEqual(self.store.load(), "synthetic-key")
        self.assertNotIn("synthetic-key", repr(self.store))
        self.store.delete()
        self.assertEqual(self.store.load(), "")
        self.store.delete()

    def test_corrupt_oversized_or_wrong_account_data_fail_closed(self):
        self.path.parent.mkdir()
        bad = [b"plaintext synthetic-key", MAGIC + b"broken", b"x" * (MAX_FILE_BYTES + 1),
               MAGIC + mock_crypt(json.dumps({"version": 2, "api_key": "synthetic-key"}).encode()),
               MAGIC + mock_crypt(json.dumps({"version": 1, "api_key": "synthetic-key", "extra": "value"}).encode())]
        for raw in bad:
            self.path.write_bytes(raw)
            with self.subTest(raw_length=len(raw)), self.assertRaises(ValueError) as error:
                self.store.load()
            self.assertNotIn("synthetic-key", str(error.exception))

    def test_empty_control_or_overlong_keys_never_write(self):
        for key in ("", "  ", "some\nkey", "some\rkey", "abc def", "x" * 4097, None):
            with self.subTest(key_length=len(key) if isinstance(key, str) else None), self.assertRaises(ValueError):
                self.store.save(key)
            self.assertFalse(self.path.exists())

    def test_protection_error_leaves_existing_key_unchanged_and_is_sanitized(self):
        self.store.save("old-key")
        before = self.path.read_bytes()
        def failed(*args, **kwargs):
            raise OSError("synthetic-key")
        failing = AiCredentialStore(self.path, crypt=failed)
        with self.assertRaises(OSError) as error:
            failing.save("synthetic-key")
        self.assertNotIn("synthetic-key", str(error.exception))
        self.assertEqual(self.path.read_bytes(), before)

    def test_atomic_replace_error_leaves_old_key_and_cleans_only_temporary_file(self):
        self.store.save("old-key")
        with patch("desktop_ui.ai_credentials.os.replace", side_effect=OSError("synthetic-key")):
            with self.assertRaises(OSError) as error:
                self.store.save("synthetic-key")
        self.assertNotIn("synthetic-key", str(error.exception))
        self.assertEqual(self.store.load(), "old-key")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_windows_dpapi_roundtrip(self):
        store = AiCredentialStore(self.path)
        store.save("synthetic-local-dpapi-check")
        self.assertNotIn(b"synthetic-local-dpapi-check", self.path.read_bytes())
        self.assertEqual(store.load(), "synthetic-local-dpapi-check")


if __name__ == "__main__":
    unittest.main()
