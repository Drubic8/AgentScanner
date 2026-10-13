"""Verify the live updater contract in CI with scoped GitHub authentication."""
import os
from pathlib import Path
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop_ui.update_package import RELEASE_API, discover_release


def release_get(token, *, get=requests.get):
    def fetch(url, **kwargs):
        # Asset downloads and redirects must never receive the CI credential.
        headers = {key: value for key, value in kwargs.pop("headers", {}).items()
                   if key.lower() != "authorization"}
        if url == RELEASE_API:
            headers["Authorization"] = f"Bearer {token}"
        kwargs["allow_redirects"] = False
        return get(url, headers=headers, **kwargs)
    return fetch


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Set GITHUB_TOKEN for the CI release discovery check")
    release = discover_release(get=release_get(token))
    print("Published update metadata verified:", release["version"], release["size"])


if __name__ == "__main__":
    main()
