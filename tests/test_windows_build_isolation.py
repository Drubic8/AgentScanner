"""Prevent unrelated native toolchains from contaminating the packaged Qt runtime."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.build_windows import build_environment, validate_native_sources


class WindowsBuildIsolationTests(unittest.TestCase):
    def test_foreign_native_paths_and_qt_overrides_are_removed(self):
        original = {"PATH": "foreign-poppler;foreign-libheif", "Path": "another-native-runtime", "SystemRoot": "Windows",
                    "TEMP": "build-temp", "PYTHONPATH": "external-python",
                    "PYTHONHOME": "other-python", "QT_PLUGIN_PATH": "external-qt",
                    "QT_QPA_PLATFORM_PLUGIN_PATH": "external-platforms"}
        with patch("scripts.build_windows.sys.prefix", "venv"), \
                patch("scripts.build_windows.sys.base_prefix", "python"):
            result = build_environment(original)
        paths = result["PATH"].split(os.pathsep)
        self.assertEqual(paths[:2], [str(Path("venv") / "Scripts"), "python"])
        self.assertIn(str(Path("Windows") / "System32"), paths)
        self.assertNotIn("foreign-poppler", result["PATH"])
        self.assertNotIn("foreign-libheif", result["PATH"])
        self.assertEqual([key for key in result if key.upper() == "PATH"], ["PATH"])
        for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
            self.assertNotIn(key, result)
        self.assertEqual(result["TEMP"], "build-temp")
        self.assertEqual(original["PYTHONHOME"], "other-python")

    def test_native_sources_allow_python_and_windows_but_reject_sibling_runtime(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            python, windows = root / "python", root / "Windows"
            binaries = [("QtCore.pyd", str(python / "Lib" / "QtCore.pyd"), "EXTENSION"),
                        ("icuuc.dll", str(windows / "System32" / "icuuc.dll"), "BINARY")]
            validate_native_sources(binaries, [python, windows])
            for foreign in (root / "poppler" / "icuuc.dll", root / "python-other" / "ucrtbase.dll"):
                with self.subTest(foreign=foreign), self.assertRaisesRegex(RuntimeError, "native library sources"):
                    validate_native_sources(binaries + [("icuuc.dll", str(foreign), "BINARY")], [python, windows])

    def test_parent_path_escape_is_rejected_but_non_native_data_is_ignored(self):
        with tempfile.TemporaryDirectory() as scratch:
            trusted = Path(scratch) / "python"
            validate_native_sources([("readme.txt", str(Path(scratch) / "readme.txt"), "DATA")], [trusted])
            with self.assertRaisesRegex(RuntimeError, "foreign.dll"):
                validate_native_sources([("foreign.dll", str(trusted / ".." / "foreign.dll"), "BINARY")], [trusted])

    def test_link_inside_python_cannot_allow_a_foreign_runtime(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            trusted, foreign = root / "python", root / "foreign"
            trusted.mkdir()
            foreign.mkdir()
            link = trusted / "linked-runtime"
            try:
                link.symlink_to(foreign, target_is_directory=True)
            except OSError:
                self.skipTest("Creating directory symlinks is unavailable on this host")
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                validate_native_sources([("icuuc.dll", str(link / "icuuc.dll"), "BINARY")], [trusted])


if __name__ == "__main__":
    unittest.main()
