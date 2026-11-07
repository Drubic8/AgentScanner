"""Protect the user's AI API key with Windows DPAPI; no plaintext fallback."""
import json
import os
from pathlib import Path
import tempfile

from .access_store import protect


MAGIC = b"ASIC-AI-KEY-1\n"
MAX_FILE_BYTES = 64 * 1024
MAX_KEY_LENGTH = 4096


def _key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_KEY_LENGTH:
        raise ValueError("Invalid AI API key")
    value = value.strip()
    if not value.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in value):
        raise ValueError("Invalid AI API key")
    return value


class AiCredentialStore:
    def __init__(self, path, crypt=protect):
        self.path = Path(path)
        self.crypt = crypt

    def __repr__(self):
        return "AiCredentialStore(<protected credential>)"

    def load(self):
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
        except FileNotFoundError:
            return ""
        except OSError:
            raise OSError("Cannot read protected AI credential") from None
        try:
            if len(raw) > MAX_FILE_BYTES or not raw.startswith(MAGIC):
                raise ValueError()
            payload = self.crypt(raw[len(MAGIC):], decrypt=True)
            if not isinstance(payload, bytes) or len(payload) > MAX_FILE_BYTES:
                raise ValueError()
            data = json.loads(payload)
            if (not isinstance(data, dict) or type(data.get("version")) is not int
                    or data["version"] != 1 or set(data) != {"version", "api_key"}):
                raise ValueError()
            return _key(data["api_key"])
        except Exception:
            raise ValueError("Cannot decrypt AI credential for this Windows account") from None

    def save(self, key):
        key = _key(key)
        try:
            payload = json.dumps({"version": 1, "api_key": key}, ensure_ascii=True).encode("utf-8")
            encrypted = self.crypt(payload)
            if not isinstance(encrypted, bytes) or not encrypted or len(encrypted) + len(MAGIC) > MAX_FILE_BYTES:
                raise ValueError()
        except Exception:
            raise OSError("Cannot protect AI credential with Windows DPAPI") from None
        name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=".ai-key-", delete=False) as stream:
                name = stream.name
                stream.write(MAGIC + encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(name, 0o600)
            os.replace(name, self.path)
        except OSError:
            raise OSError("Cannot save protected AI credential") from None
        finally:
            if name:
                try:
                    Path(name).unlink(missing_ok=True)
                except OSError:
                    pass

    def delete(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise OSError("Cannot remove protected AI credential") from None
