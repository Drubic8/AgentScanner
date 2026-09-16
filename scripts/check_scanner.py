"""Run meaningful scanner checks using only the standard-library test runner."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
for args in (["-m", "compileall", "-q", "miner_scanner", "desktop_ui", "gemini_gui.py"], ["-m", "unittest", "discover", "-s", "tests", "-v"]):
    result = subprocess.run([sys.executable, *args], cwd=root)
    if result.returncode:
        raise SystemExit(result.returncode)
