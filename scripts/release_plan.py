"""Plan platform-specific releases and assemble only verified release assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.build_windows import release_version

VERSION = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")


def android_version_info(root=ROOT):
    text = (Path(root) / "apps/android/app/build.gradle.kts").read_text(encoding="utf-8")
    names = re.findall(r'^\s*versionName\s*=\s*"([^"]+)"\s*$', text, re.M)
    codes = re.findall(r"^\s*versionCode\s*=\s*(\d+)\s*$", text, re.M)
    if len(names) != 1 or len(codes) != 1 or not VERSION.fullmatch(names[0]):
        raise ValueError("Expected one Android versionName and versionCode")
    parts = tuple(map(int, names[0].split(".")))
    code = int(codes[0])
    if parts[1] > 99 or parts[2] > 99 or code != parts[0] * 10000 + parts[1] * 100 + parts[2] or code <= 1:
        raise ValueError("Android versionCode must encode major*10000 + minor*100 + patch")
    return names[0], code


def release_versions(root=ROOT):
    windows, _ = release_version(Path(root))
    if not VERSION.fullmatch(windows):
        raise ValueError("Expected a stable Windows major.minor.patch version")
    android, code = android_version_info(root)
    return {"windows_version": windows, "android_version": android, "android_code": code,
            "include_android": android == windows}


def existing_release_kind(release, tag):
    if release is None:
        return "absent"
    if (not isinstance(release, dict) or release.get("tag_name") != tag
            or type(release.get("draft")) is not bool):
        raise ValueError("Unexpected GitHub release metadata")
    if release["draft"]:
        return "draft"
    if not isinstance(release.get("published_at"), str) or not release["published_at"]:
        raise ValueError("Published GitHub release is missing its publication time")
    return "published"


def make_plan(root, tag, existing=None):
    versions = release_versions(root)
    if tag != "v" + versions["windows_version"]:
        raise ValueError("Release tag must match the Windows release version")
    if not (Path(root) / "docs/releases" / (versions["windows_version"] + ".md")).is_file():
        raise ValueError("Release notes are missing")
    kind = existing_release_kind(existing, tag)
    build = kind == "absent"
    return {**versions, "existing_release": kind, "build_windows": build,
            "build_android": build and versions["include_android"], "publish_allowed": build}


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def fetch_existing_release(repository, tag, token, *, opener=None):
    """One exact metadata endpoint; 404 means absent, other failures stop CI."""
    if (not isinstance(repository, str) or not REPOSITORY.fullmatch(repository)
            or not isinstance(tag, str) or not tag.startswith("v") or not VERSION.fullmatch(tag[1:])):
        raise ValueError("Invalid repository or release tag")
    url = "https://api.github.com/repos/" + repository + "/releases/tags/" + urllib.parse.quote(tag, safe="")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "ASIC-Monitor-release-plan"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects())
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f"GitHub release lookup failed with HTTP {error.code}") from None
    except (OSError, urllib.error.URLError):
        raise RuntimeError("Could not check the existing GitHub release") from None
    if len(raw) > 1024 * 1024:
        raise ValueError("GitHub release metadata is too large")
    try:
        release = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Invalid GitHub release metadata") from None
    existing_release_kind(release, tag)
    return release


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_asset(directory, filename):
    source = directory / filename
    checksums = directory / "SHA256SUMS.txt"
    if source.is_symlink() or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("Missing or empty release asset: " + filename)
    if checksums.is_symlink() or not checksums.is_file():
        raise ValueError("Missing artifact checksums")
    entries = checksums.read_text(encoding="ascii").splitlines()
    if len(entries) != 1:
        raise ValueError("Expected exactly one checksum per platform artifact")
    match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", entries[0])
    if not match or match[2] != filename or file_digest(source) != match[1].lower():
        raise ValueError("Release asset checksum mismatch: " + filename)
    return source, match[1].lower()


def prepare_assets(directory, versions):
    """EXE-only release or matching EXE/APK release; never relabel an old APK."""
    directory = Path(directory)
    if type(versions.get("include_android")) is not bool or versions["include_android"] != (
            versions["windows_version"] == versions["android_version"]):
        raise ValueError("Platform release plan is inconsistent")
    specifications = [("windows", "ASIC_Monitor.exe")]
    if versions["include_android"]:
        specifications.append(("android", f"ASIC_Monitor-android-{versions['android_version']}-test.apk"))
    verified = [verified_asset(directory / platform, name) for platform, name in specifications]
    destinations = [directory / source.name for source, _ in verified]
    output = directory / "SHA256SUMS.txt"
    if output.exists() or any(path.exists() for path in destinations):
        raise ValueError("Release output already exists")
    for (source, _), destination in zip(verified, destinations):
        shutil.copyfile(source, destination)
    output.write_text("".join(digest + "  " + source.name + "\n" for source, digest in verified), encoding="ascii")
    return [path.name for path in destinations] + [output.name]


def write_outputs(path, plan):
    if path is None:
        return
    keys = ("windows_version", "android_version", "include_android", "build_windows", "build_android", "publish_allowed")
    with Path(path).open("a", encoding="utf-8") as stream:
        for key in keys:
            value = plan[key]
            stream.write(key + "=" + (str(value).lower() if isinstance(value, bool) else value) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check-publication", "android-version", "prepare-assets"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--tag", default=os.environ.get("GITHUB_REF_NAME"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=Path("release"))
    args = parser.parse_args(argv)
    if args.command == "android-version":
        print(android_version_info(args.root)[0])
        return
    if args.command == "prepare-assets":
        plan = make_plan(args.root, args.tag)
        print(json.dumps({"assets": prepare_assets(args.artifact_root, plan)}))
        return
    versions = release_versions(args.root)
    if args.tag != "v" + versions["windows_version"]:
        raise ValueError("Release tag must match the Windows release version")
    release = fetch_existing_release(args.repository, args.tag, os.environ.get("GH_TOKEN", ""))
    plan = make_plan(args.root, args.tag, release)
    write_outputs(args.github_output, plan)
    print(json.dumps(plan))


if __name__ == "__main__":
    main()
