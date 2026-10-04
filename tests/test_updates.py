"""Offline updater contracts: fake HTTP and dummy EXE files only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from desktop_ui.update_package import (ASSET_NAME, MANIFEST_URL, RELEASE_API,
    UpdateCancelled, asset_url, discover_release, download_package, parse_checksums)
from desktop_ui.update_installer import apply_transaction, file_hash, validate_plan

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


PAYLOAD = b"MZ" + b"demo-release" * 200
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class Response:
    def __init__(self, body, *, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def close(self): self.closed = True
    def raise_for_status(self):
        if self.status_code >= 400: raise ValueError("HTTP failure")
    def iter_content(self, size):
        for start in range(0, len(self.body), size): yield self.body[start:start+size]


def metadata(version="2.2.2"):
    return dict(version=version, url=asset_url(version), sha256=DIGEST, size=len(PAYLOAD))


class UpdatePackageTests(unittest.TestCase):
    def test_published_release_wins_over_next_manifest_and_urls_are_pinned(self):
        release = dict(tag_name="v2.2.2", published_at="2026-10-04", draft=False, prerelease=False, body="Release notes",
            assets=[dict(name=ASSET_NAME, size=len(PAYLOAD), browser_download_url=asset_url("2.2.2"), digest="sha256:"+DIGEST),
                    dict(name="SHA256SUMS.txt", browser_download_url=asset_url("2.2.2", "SHA256SUMS.txt"))])
        responses = {RELEASE_API: json.dumps(release).encode(),
            asset_url("2.2.2", "SHA256SUMS.txt"): f"{DIGEST}  {ASSET_NAME}\n".encode(),
            MANIFEST_URL: json.dumps(dict(version="2.2.3", url=asset_url("2.2.3"), changelog="Not released")).encode()}
        get = Mock(side_effect=lambda url, **kw: Response(responses[url]))
        result = discover_release(get=get)
        self.assertEqual(result["version"], "2.2.2")
        self.assertEqual(result["url"], asset_url("2.2.2"))
        self.assertEqual(result["sha256"], DIGEST)
        self.assertEqual(result["changelog"], "Release notes")
        release["assets"][0]["digest"] = "sha256:" + "0" * 64
        responses[RELEASE_API] = json.dumps(release).encode()
        with self.assertRaisesRegex(ValueError, "disagree"):
            discover_release(get=get)

    def test_missing_duplicate_or_foreign_asset_is_rejected(self):
        for assets in ([], [dict(name=ASSET_NAME, browser_download_url="https://evil.invalid/a.exe")],
                       [dict(name=ASSET_NAME)] * 2):
            body = dict(tag_name="v2.2.2", published_at="yes", draft=False, prerelease=False, assets=assets)
            with self.subTest(assets=assets), self.assertRaises(ValueError):
                discover_release(get=lambda *a, **kw: Response(json.dumps(body).encode()))

    def test_checksum_duplicate_and_missing_entries_are_rejected(self):
        line = f"{DIGEST}  {ASSET_NAME}\n".encode()
        self.assertEqual(parse_checksums(line), DIGEST)
        for value in (b"", line+line, b"not a checksum"):
            with self.assertRaises(ValueError): parse_checksums(value)

    def test_download_checks_exact_bytes_and_reports_progress(self):
        with TemporaryDirectory() as directory:
            progress = []
            path = download_package(metadata(), directory, cancelled=Event(),
                progress=lambda done, total: progress.append((done, total)), get=lambda *a, **kw: Response(PAYLOAD))
            self.assertEqual(path.read_bytes(), PAYLOAD)
            self.assertEqual(progress[-1], (len(PAYLOAD), len(PAYLOAD)))
            self.assertEqual(file_hash(path), DIGEST)

    def test_corruption_truncation_oversize_and_non_exe_leave_no_package(self):
        for payload in (PAYLOAD[:-1], PAYLOAD+b"extra", b"X"*len(PAYLOAD), b"not-exe"*400):
            with self.subTest(payload=payload[:8]), TemporaryDirectory() as directory:
                release = metadata()
                if payload.startswith(b"not-exe"):
                    release.update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
                with self.assertRaises(ValueError):
                    download_package(release, directory, cancelled=Event(), get=lambda *a, **kw: Response(payload))
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cancel_and_untrusted_redirect_never_leave_an_executable(self):
        with TemporaryDirectory() as directory:
            cancelled = Event(); cancelled.set()
            with self.assertRaises(UpdateCancelled):
                download_package(metadata(), directory, cancelled=cancelled, get=lambda *a, **kw: Response(PAYLOAD))
            get = Mock(return_value=Response(b"", status=302, headers={"Location": "https://evil.invalid/file.exe"}))
            with self.assertRaisesRegex(ValueError, "redirect"):
                download_package(metadata(), directory, cancelled=Event(), get=get)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(list(Path(directory).iterdir()), [])


class UpdateInstallerTests(unittest.TestCase):
    def transaction(self, root):
        # Windows runners redirect their temporary directory through a junction.
        # Production prepare_update resolves both paths before writing the plan.
        root = root.resolve()
        target = root / "installed.exe"
        target.write_bytes(b"MZ-original-program")
        stage = root / "update-test"; stage.mkdir()
        candidate = stage / "package.exe"; candidate.write_bytes(PAYLOAD)
        token = "a" * 32
        return stage, dict(target=str(target), candidate=str(candidate),
            backup=str(target.with_name(target.name+".previous-"+token+".bak")),
            temporary=str(target.with_name(target.name+".update-"+token+".tmp")),
            version="2.2.2", sha256=DIGEST, old_sha256=file_hash(target), token=token, parents=[123])

    def test_success_keeps_original_backup_and_requires_matching_startup_receipt(self):
        with TemporaryDirectory() as directory:
            stage, plan = self.transaction(Path(directory))
            child = Mock(); child.poll.return_value = None; child.pid = 456
            def launch(args):
                (stage / "startup.json").write_text(json.dumps(dict(version="2.2.2", token=plan["token"])))
                return child
            wait = Mock()
            self.assertTrue(apply_transaction(plan, stage, wait=wait, verify_version=lambda p: "2.2.2", launch=launch))
            wait.assert_called_once_with([123])
            self.assertEqual(Path(plan["target"]).read_bytes(), PAYLOAD)
            self.assertEqual(Path(plan["backup"]).read_bytes(), b"MZ-original-program")
            self.assertFalse(Path(plan["candidate"]).exists())
            self.assertTrue(json.loads((stage / "status.json").read_text())["ok"])

    def test_startup_failure_restores_original_and_restarts_it(self):
        with TemporaryDirectory() as directory:
            stage, plan = self.transaction(Path(directory))
            child = Mock(); child.poll.return_value = 1
            launch = Mock(return_value=child)
            with self.assertRaisesRegex(RuntimeError, "exited"):
                apply_transaction(plan, stage, wait=lambda p: None, verify_version=lambda p: "2.2.2", launch=launch, stop=Mock())
            self.assertEqual(Path(plan["target"]).read_bytes(), b"MZ-original-program")
            self.assertIn("--update-rollback", launch.call_args.args[0])
            self.assertTrue(json.loads((stage / "status.json").read_text())["restored"])

    def test_package_change_old_exe_change_and_wrong_pe_version_abort_before_swap(self):
        for changed in ("candidate", "target", "version"):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                stage, plan = self.transaction(Path(directory))
                if changed != "version": Path(plan[changed]).write_bytes(b"changed")
                before = Path(plan["target"]).read_bytes()
                launch = Mock()
                with self.assertRaises(ValueError):
                    apply_transaction(plan, stage, wait=lambda p: None, verify_version=lambda p: "1.0.0", launch=launch)
                self.assertEqual(Path(plan["target"]).read_bytes(), before)
                self.assertFalse(Path(plan["backup"]).exists())
                launch.assert_not_called()

    def test_failed_replacement_restores_backup(self):
        import os
        with TemporaryDirectory() as directory:
            stage, plan = self.transaction(Path(directory))
            replace = os.replace
            def fail_second(source, destination):
                if Path(source) == Path(plan["temporary"]): raise OSError("disk failure")
                return replace(source, destination)
            with patch("desktop_ui.update_installer.os.replace", side_effect=fail_second), self.assertRaises(OSError):
                apply_transaction(plan, stage, wait=lambda p: None, verify_version=lambda p: "2.2.2", launch=Mock())
            self.assertEqual(Path(plan["target"]).read_bytes(), b"MZ-original-program")

    def test_invalid_backup_and_existing_transaction_paths_are_rejected(self):
        with TemporaryDirectory() as directory:
            stage, plan = self.transaction(Path(directory))
            invalid = {**plan, "backup": str(stage / "foreign.exe")}
            with self.assertRaises(ValueError): validate_plan(invalid, stage)
            Path(plan["backup"]).write_bytes(b"another transaction")
            with self.assertRaises(ValueError): validate_plan(plan, stage)

    def test_startup_timeout_or_wrong_receipt_stops_new_process_and_rolls_back(self):
        for wrong_receipt in (False, True):
            with self.subTest(wrong_receipt=wrong_receipt), TemporaryDirectory() as directory:
                stage, plan = self.transaction(Path(directory))
                child = Mock(); child.poll.return_value = None
                def launch(args):
                    if wrong_receipt:
                        (stage / "startup.json").write_text(json.dumps(dict(version="2.2.2", token="wrong")))
                    return child
                stop = Mock()
                with self.assertRaises((TimeoutError, ValueError)):
                    apply_transaction(plan, stage, wait=lambda p: None, verify_version=lambda p: "2.2.2",
                                      launch=launch, stop=stop, startup_timeout=1 if wrong_receipt else 0)
                stop.assert_called_once_with(child)
                self.assertEqual(Path(plan["target"]).read_bytes(), b"MZ-original-program")


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("PyQt6", "pandas", "fpdf")), "Desktop dependencies not installed")
class UpdateUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gemini_gui
        cls.gui = gemini_gui
        cls.app = gemini_gui.QApplication.instance() or gemini_gui.QApplication([])

    def test_update_installation_waits_for_device_operations_and_requires_confirmation(self):
        from miner_scanner.access import AccessProfiles
        with patch.object(self.gui.AccessStore, "load", return_value=AccessProfiles([])):
            window = self.gui.GeminiApp(settings={}, ranges=[])
        self.addCleanup(window.close)
        window.staged_update = (Path("dummy-plan.json"), "2.2.3")
        worker = Mock(); worker.isRunning.return_value = True
        window.workers = [worker]
        with patch.object(self.gui.QMessageBox, "information") as information, patch.object(window, "close") as close:
            window.install_downloaded_update()
            information.assert_called_once()
            close.assert_not_called()
        worker.isRunning.return_value = False
        with patch.object(self.gui.QMessageBox, "question", return_value=self.gui.QMessageBox.StandardButton.No), patch.object(window, "close") as close:
            window.install_downloaded_update()
            close.assert_not_called()
        with patch.object(self.gui.QMessageBox, "question", return_value=self.gui.QMessageBox.StandardButton.Yes), patch.object(window, "close") as close:
            window.install_downloaded_update()
            close.assert_called_once()
        window.install_on_close = False
