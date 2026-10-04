"""Published GitHub release discovery and bounded, verified EXE downloads."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit

import requests

REPOSITORY = "Drubic8/AgentScanner"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MANIFEST_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/main/version.json"
ASSET_NAME = "ASIC_Monitor.exe"
MAX_EXE_BYTES = 300 * 1024 * 1024


class UpdateCancelled(Exception):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", value):
        raise ValueError("Invalid update version")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in parts):
        raise ValueError("Invalid update version")
    return parts


def asset_url(version, name=ASSET_NAME):
    version_tuple(version)
    return f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}"


def validate_manifest(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid update manifest")
    version_tuple(data.get("version"))
    if data.get("url") not in (asset_url(data["version"]),
            f"https://github.com/{REPOSITORY}/releases/latest/download/{ASSET_NAME}"):
        raise ValueError("Unknown update source")
    return data


def _allowed_url(url, initial):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    return url == initial or parsed.hostname in ("release-assets.githubusercontent.com", "objects.githubusercontent.com")


def _response(url, *, get=requests.get):
    initial = url
    for _ in range(6):
        if not _allowed_url(url, initial):
            raise ValueError("Unexpected update redirect")
        response = get(url, stream=True, allow_redirects=False, timeout=(5, 10),
                       headers={"User-Agent": "ASIC-Monitor-Updater", "Accept": "application/vnd.github+json"})
        if response.status_code in (301, 302, 303, 307, 308):
            destination = response.headers.get("Location", "")
            response.close()
            url = destination
            continue
        try:
            response.raise_for_status()
        except Exception:
            response.close()
            raise
        return response
    raise ValueError("Too many update redirects")


def read_bytes(url, limit, *, get=requests.get):
    deadline = time.monotonic() + 30
    data = bytearray()
    with _response(url, get=get) as response:
        for chunk in response.iter_content(65536):
            if len(data) + len(chunk) > limit or time.monotonic() > deadline:
                raise ValueError("Update response exceeds its limit")
            data.extend(chunk)
    return bytes(data)


def parse_checksums(content):
    matches = []
    for line in content.decode("ascii").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]ASIC_Monitor\.exe", line)
        if match:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise ValueError("Missing or duplicate EXE checksum")
    return matches[0]


def discover_release(manifest_url=MANIFEST_URL, *, get=requests.get):
    # The latest *published* release is authoritative; main can already contain the next version.
    release = json.loads(read_bytes(RELEASE_API, 1024 * 1024, get=get))
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("Update release is not published")
    tag = release.get("tag_name", "")
    if not tag.startswith("v") or not release.get("published_at"):
        raise ValueError("Invalid release tag")
    version = tag[1:]
    version_tuple(version)
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("Invalid release assets")
    def one_asset(name):
        found = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
        if len(found) != 1 or found[0].get("browser_download_url") != asset_url(version, name):
            raise ValueError("Missing or unexpected release asset")
        return found[0]
    executable, checksums = one_asset(ASSET_NAME), one_asset("SHA256SUMS.txt")
    size = executable.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or not 1024 <= size <= MAX_EXE_BYTES:
        raise ValueError("Invalid EXE size")
    digest = parse_checksums(read_bytes(checksums["browser_download_url"], 65536, get=get))
    api_digest = executable.get("digest")
    if api_digest is not None and api_digest != "sha256:" + digest:
        raise ValueError("Release checksums disagree")
    data = dict(version=version, url=executable["browser_download_url"], sha256=digest, size=size,
                changelog=release.get("body") or "", changelog_en=release.get("body") or "")
    # Changelog localisation is optional and cannot change the asset or version being installed.
    if manifest_url == MANIFEST_URL:
        try:
            manifest = validate_manifest(json.loads(read_bytes(MANIFEST_URL, 65536, get=get)))
            if manifest["version"] == version:
                for key in ("changelog", "changelog_en"):
                    if isinstance(manifest.get(key), str):
                        data[key] = manifest[key]
        except (requests.RequestException, ValueError, UnicodeError):
            pass
    return data


def download_package(release, cache, *, cancelled, progress=lambda received, total: None, get=requests.get):
    version_tuple(release.get("version"))
    if release.get("url") != asset_url(release["version"]):
        raise ValueError("Unknown EXE source")
    size, digest = release.get("size"), release.get("sha256", "")
    if isinstance(size, bool) or not isinstance(size, int) or not 1024 <= size <= MAX_EXE_BYTES:
        raise ValueError("Invalid EXE size")
    if not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("Invalid EXE checksum")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="update-", dir=cache))
    path = staging / "package.exe"
    received, checksum = 0, hashlib.sha256()
    deadline = time.monotonic() + 600
    try:
        with _response(release["url"], get=get) as response, path.open("xb") as stream:
            for chunk in response.iter_content(256 * 1024):
                if cancelled.is_set():
                    raise UpdateCancelled()
                if time.monotonic() > deadline or received + len(chunk) > size:
                    raise ValueError("EXE download exceeds its limit")
                stream.write(chunk)
                checksum.update(chunk)
                received += len(chunk)
                progress(received, size)
            stream.flush()
            os.fsync(stream.fileno())
        if cancelled.is_set():
            raise UpdateCancelled()
        if received != size or checksum.hexdigest() != digest:
            raise ValueError("Downloaded EXE checksum or size mismatch")
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("Downloaded file is not a Windows executable")
        return path
    except Exception:
        path.unlink(missing_ok=True)
        staging.rmdir()
        raise
