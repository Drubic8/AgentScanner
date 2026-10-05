"""Run meaningful scanner checks using only the standard-library test runner."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="asic-monitor-tests-") as scratch:
    env = dict(os.environ, ASIC_MONITOR_DATA_DIR=str(Path(scratch) / "settings"),
               MINER_SCANNER_DATA_DIR=str(Path(scratch) / "scanner"))
    for args in (["-m", "compileall", "-q", "miner_scanner", "desktop_ui", "gemini_gui.py"], ["-m", "unittest", "discover", "-s", "tests", "-v"]):
        result = subprocess.run([sys.executable, *args], cwd=root, env=env)
        if result.returncode:
            raise SystemExit(result.returncode)
