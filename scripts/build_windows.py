"""Validate, test and build the single-file Windows release without publishing it."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def build_environment(environment=None):
    """Keep native DLL discovery inside Python and Windows, independent of the caller."""
    environment = dict(os.environ if environment is None else environment)
    windows = Path(next((value for key, value in environment.items() if key.upper() == "SYSTEMROOT"), r"C:\Windows"))
    paths = [Path(sys.prefix) / "Scripts", Path(sys.base_prefix),
             windows / "System32", windows, windows / "System32" / "Wbem",
             windows / "System32" / "WindowsPowerShell" / "v1.0"]
    for key in list(environment):
        if key.upper() in {"PATH", "PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"}:
            environment.pop(key)
    environment["PATH"] = os.pathsep.join(dict.fromkeys(str(path) for path in paths))
    return environment


def validate_native_sources(binaries, allowed_roots=None):
    """Reject native libraries accidentally discovered in unrelated application runtimes."""
    if allowed_roots is None:
        allowed_roots = (sys.prefix, sys.base_prefix, os.environ.get("SystemRoot", r"C:\Windows"))
    roots = [Path(path).resolve() for path in allowed_roots]
    unexpected = []
    for destination, source, kind in binaries:
        if kind not in {"BINARY", "EXTENSION"}:
            continue
        resolved = Path(source).resolve()
        if not any(resolved.is_relative_to(root) for root in roots):
            unexpected.append(f"{destination}: {resolved}")
    if unexpected:
        raise RuntimeError("Untrusted native library sources in Windows build:\n" + "\n".join(unexpected))


def release_version(root=ROOT):
    manifest = json.loads((root / "version.json").read_text(encoding="utf-8"))
    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    module = ast.parse((root / "gemini_gui.py").read_text(encoding="utf-8"))
    gui = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "CURRENT_VERSION" for target in node.targets))
    if not (gui == package == manifest["version"]):
        raise ValueError(f"Versions disagree: GUI={gui}, package={package}, manifest={manifest['version']}")
    parts = tuple(int(part) for part in gui.split("."))
    if len(parts) != 3 or any(part < 0 or part > 65535 for part in parts):
        raise ValueError("Expected major.minor.patch, each in 0..65535")
    return gui, parts


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows EXE on Windows.")
    version, parts = release_version()
    subprocess.run([sys.executable, "scripts/check_scanner.py"], cwd=ROOT, check=True)
    work = ROOT / "build-windows"
    work.mkdir(exist_ok=True)
    resource = f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts + (0,)!r}, prodvers={parts + (0,)!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'ASIC Monitor'),
    StringStruct('FileDescription', 'ASIC Monitor - Local ASIC monitoring'),
    StringStruct('FileVersion', '{version}'),
    StringStruct('InternalName', 'ASIC_Monitor'),
    StringStruct('OriginalFilename', 'ASIC_Monitor.exe'),
    StringStruct('ProductName', 'ASIC Monitor'),
    StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
'''
    (work / "version_info.txt").write_text(resource, encoding="utf-8")
    isolated_env = build_environment()
    # A fresh analysis must not reuse DLL paths collected from an earlier caller's PATH.
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--workpath", str(work / "pyinstaller"),
                    "--distpath", str(ROOT / "dist"), "ASIC_Monitor.spec"], cwd=ROOT, check=True,
                   env=isolated_env)
    executable = ROOT / "dist" / "ASIC_Monitor.exe"
    # Run the packaged program, not Python source, in explicit offline smoke mode.
    output = ROOT / "artifacts" / "exe-smoke"
    result_path = output / "result.json"
    if result_path.exists():
        result_path.unlink()
    result = subprocess.run([str(executable), "--smoke-test", str(output)], cwd=ROOT, timeout=120,
                            env={**isolated_env, "QT_QPA_PLATFORM": "offscreen"})
    if result.returncode or not result_path.exists():
        raise RuntimeError(f"EXE smoke check failed; inspect {output}")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    if not report.get("ok") or report.get("version") != version:
        raise RuntimeError(f"EXE smoke check failed: {report}")
    subprocess.run([sys.executable, "scripts/check_windows_update.py"], cwd=ROOT, check=True, timeout=180)
    subprocess.run([sys.executable, "scripts/check_windows_storage.py"], cwd=ROOT, check=True, timeout=300)
    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (ROOT / "dist" / "SHA256SUMS.txt").write_text(f"{digest}  ASIC_Monitor.exe\n", encoding="ascii")
    print(f"Built and checked: {executable} ({executable.stat().st_size / 1024**2:.1f} MiB)")
    print(f"Version: {version}; SHA256: {digest}")


if __name__ == "__main__":
    main()
