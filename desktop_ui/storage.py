"""Desktop sidecar storage and one-time migration from the former user directory.

This module does not change the scanner/agent database or Android storage.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4

MARKER = '.asic-monitor-storage.json'
FILES = ('ip_ranges.json', 'app_settings.json', 'access_profiles.dat')


def data_directory():
    override = os.environ.get('ASIC_MONITOR_DATA_DIR')
    if override:
        return Path(override)
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def former_data_directory():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / '.local' / 'share'))) / 'ASICMonitor'


def atomic_write(path, payload):
    """Keep the old file intact if writing or replacement fails."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name + '-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _valid_payload(path):
    if not path.is_file():
        return None
    payload = path.read_bytes()
    if path.name == 'access_profiles.dat':
        return payload if payload.startswith(b'ASIC-ACCESS-1\n') else None
    try:
        value = json.loads(payload.decode('utf-8-sig'))
    except (UnicodeError, ValueError):
        return None
    types = (list, dict) if path.name == 'ip_ranges.json' else (dict,)
    return payload if isinstance(value, types) else None


def initialize_storage():
    """Use sidecars after migration; never re-import AppData on later launches.

    On the first launch the newer valid file wins. Existing sidecars which are
    replaced are backed up, and the former directory is never changed/deleted.
    An explicit override is isolated and never imports personal files.
    """
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    # Fail visibly before opening the GUI instead of secretly saving elsewhere.
    with tempfile.TemporaryFile(dir=directory) as stream:
        stream.write(b'ASIC Monitor storage check')
        stream.flush()
    if os.environ.get('ASIC_MONITOR_DATA_DIR') or (directory / MARKER).exists():
        return directory
    old = former_data_directory()
    if old.resolve() == directory.resolve():
        return directory
    report = {'storage_version': 1, 'files': {}}
    backup = directory / '.settings-backup' / uuid4().hex
    for name in FILES:
        target, previous = directory / name, old / name
        local_data, old_data = _valid_payload(target), _valid_payload(previous)
        use_old = old_data is not None and (local_data is None or previous.stat().st_mtime_ns > target.stat().st_mtime_ns)
        if use_old:
            if target.exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / name)
            atomic_write(target, old_data)
            report['files'][name] = {'source': str(previous), 'backup': str(backup / name) if (backup / name).exists() else None}
        elif local_data is not None:
            report['files'][name] = {'source': str(target)}
        elif target.exists() or previous.exists():
            raise ValueError(f'Invalid settings file: {target if target.exists() else previous}')
    atomic_write(directory / MARKER, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    return directory
