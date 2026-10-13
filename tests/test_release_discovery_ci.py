"""The CI credential is confined to the exact release metadata endpoint."""
import unittest
from unittest.mock import Mock

from desktop_ui.update_package import MANIFEST_URL, RELEASE_API, asset_url
from scripts.check_published_release import release_get


class ReleaseDiscoveryCITests(unittest.TestCase):
    def test_authentication_is_scoped_and_redirects_are_manual(self):
        transport = Mock()
        fetch = release_get("test-ci-token", get=transport)
        headers = {"User-Agent": "test", "aUtHoRiZaTiOn": "stale-token"}
        urls = [RELEASE_API, MANIFEST_URL, asset_url("2.4.0", "SHA256SUMS.txt"),
                "https://release-assets.githubusercontent.com/asset",
                RELEASE_API + "?unexpected=1", RELEASE_API + "/other",
                "https://api.github.com.evil.example/releases/latest"]
        for url in urls:
            with self.subTest(url=url):
                fetch(url, headers=headers, stream=True, allow_redirects=True, timeout=(5, 10))
                kwargs = transport.call_args.kwargs
                self.assertEqual(kwargs["headers"].get("Authorization"),
                                 "Bearer test-ci-token" if url == RELEASE_API else None)
                self.assertEqual(kwargs["headers"]["User-Agent"], "test")
                self.assertFalse(kwargs["allow_redirects"])
                self.assertEqual(kwargs["timeout"], (5, 10))
        self.assertEqual(headers["aUtHoRiZaTiOn"], "stale-token")


if __name__ == "__main__":
    unittest.main()
