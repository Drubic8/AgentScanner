"""Per-Windows-user DPAPI storage. Never falls back to plaintext."""
import ctypes
from ctypes import wintypes
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile

from miner_scanner.access import AccessProfile, AccessProfiles, standard_profiles

MAGIC = b'ASIC-ACCESS-1\n'


def protect(data, *, decrypt=False):
    if os.name != 'nt':
        raise OSError('Сохранение паролей доступно через защиту Windows DPAPI.')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buffer), Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError('Windows не смогла защитить или прочитать профили доступа.')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


class AccessStore:
    def __init__(self, path, crypt=protect):
        self.path = Path(path)
        self.crypt = crypt

    def load(self):
        if not self.path.exists():
            return AccessProfiles(standard_profiles())
        raw = self.path.read_bytes()
        if not raw.startswith(MAGIC):
            raise ValueError('Неподдерживаемый файл профилей доступа.')
        data = json.loads(self.crypt(raw[len(MAGIC):], decrypt=True))
        if data.get('version') != 1:
            raise ValueError('Неподдерживаемая версия профилей доступа.')
        profiles = [AccessProfile(**item) for item in data['profiles']]
        if data.get('defaults_revision', 1) < 2:
            # Migrate only our former empty template. Preserve custom accounts,
            # passwords, scopes, disabled profiles and intentionally empty lists.
            profiles = [replace(p, password='super') if (
                p.name == 'WhatsMiner — все устройства' and p.family == 'whatsminer'
                and p.username == 'super' and not p.password and p.targets == '*'
                and p.enabled) else p for p in profiles]
        return AccessProfiles(profiles)

    def save(self, profiles):
        payload = json.dumps({'version': 1, 'defaults_revision': 2,
                              'profiles': [asdict(p) for p in profiles.profiles]}, ensure_ascii=True).encode()
        encrypted = MAGIC + self.crypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as stream:
                name = stream.name
                stream.write(encrypted)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)
