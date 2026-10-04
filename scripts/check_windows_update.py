"""Exercise the packaged updater offline, using only disposable EXE copies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_ui.update_installer import exe_version, file_hash, launch_updater, prepare_update, process_image


def main():
    if sys.platform != "win32":
        raise SystemExit("The packaged update check requires Windows")
    executable = ROOT / "dist" / "ASIC_Monitor.exe"
    output = ROOT / "artifacts" / "exe-update"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="update-test-", dir=output) as scratch:
        directory = Path(scratch) / "\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 EXE"
        directory.mkdir()
        directory = directory.resolve()
        target = directory / "ASIC Monitor.exe"
        stage = directory / "update-check"
        stage.mkdir()
        candidate = stage / "package.exe"
        shutil.copyfile(executable, target)
        shutil.copyfile(executable, candidate)
        version, digest = exe_version(executable), file_hash(executable)
        plan_path = prepare_update(candidate, target, version, digest)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        # The real helper must wait for an actual process to exit before replacing the copy.
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        plan["parents"] = [parent.pid]
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        previous = {key: os.environ.get(key) for key in ("ASIC_MONITOR_DATA_DIR", "MINER_SCANNER_DATA_DIR", "QT_QPA_PLATFORM")}
        os.environ.update(ASIC_MONITOR_DATA_DIR=str(directory / "settings"),
                          MINER_SCANNER_DATA_DIR=str(directory / "scanner"), QT_QPA_PLATFORM="offscreen")
        helper = None
        try:
            helper = launch_updater(plan_path)
            helper.wait(timeout=120)
            status_path = stage / "status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            # Only terminate the disposable program launched by this exact transaction.
            pid = status.get("pid")
            if pid and process_image(pid) == target:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=True, timeout=20)
            if helper.returncode or not status.get("ok") or status.get("version") != version:
                raise RuntimeError(f"Packaged update failed: {status}")
            if file_hash(target) != digest or file_hash(Path(plan["backup"])) != digest or candidate.exists():
                raise RuntimeError("Update files or backup do not match")
            report = dict(ok=True, version=version, unicode_paths=True, backup_verified=True)
            (output / "result.json").write_text(json.dumps(report), encoding="utf-8")
            print(f"Packaged update and startup confirmation passed: {version}")
        finally:
            parent.wait(timeout=10)
            if helper is not None and helper.poll() is None:
                subprocess.run(["taskkill", "/PID", str(helper.pid), "/T", "/F"], capture_output=True, check=False, timeout=20)
                helper.wait(timeout=20)
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    main()
