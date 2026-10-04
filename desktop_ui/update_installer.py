"""Unprivileged Windows update helper. No shell scripts, Qt, or device commands."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import time

from .update_package import MAX_EXE_BYTES, version_tuple


def file_hash(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Update files must not be links")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_EXE_BYTES:
            raise ValueError("Invalid update file")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError("Update file changed during verification")
    return digest


def exe_version(path):
    """Read the PE version resource without loading or executing the candidate."""
    from ctypes import wintypes
    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    library.GetFileVersionInfoW.restype = wintypes.BOOL
    library.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    library.VerQueryValueW.restype = wintypes.BOOL
    size = library.GetFileVersionInfoSizeW(str(path), None)
    if not size or size > 1024 * 1024:
        raise ValueError("Missing Windows EXE version")
    buffer = ctypes.create_string_buffer(size)
    value, length = ctypes.c_void_p(), wintypes.UINT()
    if not library.GetFileVersionInfoW(str(path), 0, size, buffer) or not library.VerQueryValueW(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
        raise ValueError("Unreadable Windows EXE version")
    if length.value < 13 * 4:
        raise ValueError("Invalid Windows EXE version")
    info = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD * 13)).contents
    if info[0] != 0xFEEF04BD:
        raise ValueError("Invalid Windows version signature")
    return f"{info[2] >> 16}.{info[2] & 65535}.{info[3] >> 16}"


def process_image(pid):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        return Path(buffer.value) if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)) else None
    finally:
        kernel.CloseHandle(handle)


def parent_processes(target):
    pids = [os.getpid()]
    parent = os.getppid()
    if sys.platform == "win32" and process_image(parent) == Path(target):
        pids.append(parent)  # PyInstaller's outer one-file bootloader also holds the EXE open.
    return pids


def wait_for_processes(pids, timeout=120):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    deadline = time.monotonic() + timeout
    for pid in pids:
        if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
            raise ValueError("Invalid updater parent process")
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # Process already exited.
                continue
            raise OSError("Cannot wait for the running application")
        try:
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if kernel.WaitForSingleObject(handle, remaining) != 0:
                raise TimeoutError("Application did not close in time")
        finally:
            kernel.CloseHandle(handle)


def _write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temporary, path)


def prepare_update(candidate, target, version, digest):
    candidate, target = Path(candidate).resolve(), Path(target).resolve()
    version_tuple(version)
    if target.suffix.lower() != ".exe" or candidate == target or not candidate.is_file():
        raise ValueError("Invalid installation path")
    if file_hash(candidate) != digest or exe_version(candidate) != version:
        raise ValueError("Downloaded EXE does not match the selected release")
    token = secrets.token_hex(16)
    backup = target.with_name(target.name + ".previous-" + token + ".bak")
    # Probe write permission without modifying the installed program.
    probe = target.with_name(target.name + ".update-" + token + ".tmp")
    with probe.open("xb"):
        pass
    probe.unlink()
    staging = candidate.parent
    helper = staging / "updater.exe"
    old_digest = file_hash(target)
    shutil.copyfile(target, helper)
    if file_hash(helper) != old_digest:
        helper.unlink(missing_ok=True)
        raise ValueError("Cannot prepare the update helper")
    plan = dict(target=str(target), candidate=str(candidate), backup=str(backup), temporary=str(probe),
                version=version, sha256=digest, old_sha256=old_digest, token=token,
                parents=parent_processes(target))
    path = staging / "plan.json"
    _write_json(path, plan)
    return path


def launch_updater(plan_path):
    path = Path(plan_path)
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return subprocess.Popen([str(path.parent / "updater.exe"), "--apply-update", str(path)],
                            cwd=str(path.parent), close_fds=True, startupinfo=startup,
                            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)


def validate_plan(plan, staging):
    staging = Path(staging).resolve()
    target, candidate = Path(plan["target"]), Path(plan["candidate"])
    token = plan.get("token", "")
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid update transaction")
    version_tuple(plan["version"])
    for key in ("sha256", "old_sha256"):
        digest = plan.get(key, "")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid update digest")
    parents = plan.get("parents")
    if not isinstance(parents, list) or not 1 <= len(parents) <= 2 or any(type(pid) is not int or pid <= 0 for pid in parents):
        raise ValueError("Invalid updater parent processes")
    if not target.is_absolute() or target.suffix.lower() != ".exe" or candidate != staging / "package.exe":
        raise ValueError("Invalid update transaction paths")
    if Path(plan["backup"]) != target.with_name(target.name + ".previous-" + token + ".bak"):
        raise ValueError("Invalid update backup path")
    if Path(plan["temporary"]) != target.with_name(target.name + ".update-" + token + ".tmp"):
        raise ValueError("Invalid update temporary path")
    if any(Path(p).is_symlink() for p in (plan["target"], plan["candidate"], plan["backup"], plan["temporary"])):
        raise ValueError("Update paths must not be links")
    if Path(plan["backup"]).exists() or Path(plan["temporary"]).exists():
        raise ValueError("Update transaction already exists")


def apply_transaction(plan, staging, *, wait=wait_for_processes, verify_version=exe_version,
                      launch=None, stop=None, startup_timeout=60):
    """Verify again, replace atomically, and restore the old EXE when startup fails."""
    staging = Path(staging).resolve()
    validate_plan(plan, staging)
    target, candidate, backup, temporary = (Path(plan[key]) for key in ("target", "candidate", "backup", "temporary"))
    receipt, status = staging / "startup.json", staging / "status.json"
    wait(plan["parents"])
    if file_hash(target) != plan["old_sha256"] or file_hash(candidate) != plan["sha256"]:
        raise ValueError("Application or update package changed")
    if verify_version(candidate) != plan["version"]:
        raise ValueError("Update EXE version mismatch")
    launch = launch or (lambda args: subprocess.Popen(args, cwd=str(target.parent), close_fds=True))
    def stop_process(child):
        if child.poll() is None:
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"], capture_output=True, timeout=15, check=False)
            child.wait(timeout=15)
    stop = stop or stop_process
    child, moved = None, False
    try:
        with candidate.open("rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        if file_hash(temporary) != plan["sha256"]:
            raise ValueError("Installed EXE copy failed verification")
        os.replace(target, backup)
        moved = True
        os.replace(temporary, target)
        receipt.unlink(missing_ok=True)
        child = launch([str(target), "--update-startup", str(receipt), "--update-token", plan["token"]])
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError("Updated application exited before startup confirmation")
            if receipt.exists():
                acknowledgement = json.loads(receipt.read_text(encoding="utf-8"))
                if acknowledgement == {"version": plan["version"], "token": plan["token"]}:
                    _write_json(status, dict(ok=True, version=plan["version"], backup=str(backup), pid=child.pid))
                    try:
                        candidate.unlink(missing_ok=True)
                    except OSError:
                        pass  # Cache cleanup must not roll back a healthy application.
                    return True
                raise ValueError("Unexpected update startup receipt")
            time.sleep(0.2)
        raise TimeoutError("Updated application did not confirm startup")
    except Exception as exc:
        if child is not None:
            stop(child)
        if moved:
            os.replace(backup, target)
        temporary.unlink(missing_ok=True)
        _write_json(status, dict(ok=False, restored=moved, error=str(exc)))
        if moved:
            launch([str(target), "--update-rollback", str(status)])
        raise


def run_helper(plan_path):
    path = Path(plan_path).resolve()
    try:
        if path.name != "plan.json" or path.stat().st_size > 16384:
            raise ValueError("Invalid updater plan")
        plan = json.loads(path.read_text(encoding="utf-8"))
        apply_transaction(plan, path.parent)
        return 0
    except Exception as exc:
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, "ASIC Monitor update failed.\n" + str(exc), "ASIC Monitor", 0x10)
        return 1


def acknowledge_startup(path, token, version):
    path = Path(path).resolve()
    if path.name != "startup.json" or not path.parent.name.startswith("update-"):
        raise ValueError("Invalid startup receipt path")
    plan_path = path.parent / "plan.json"
    if plan_path.stat().st_size > 16384:
        raise ValueError("Invalid updater plan")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("token") != token or plan.get("version") != version or Path(plan["target"]).resolve() != Path(sys.executable).resolve():
        raise ValueError("Unexpected updater startup request")
    _write_json(path, dict(version=version, token=token))
