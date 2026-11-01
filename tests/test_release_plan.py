"""Release builds preserve platform versions and never overwrite an existing release."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
import urllib.error

from scripts.release_plan import (NoRedirects, android_version_info, fetch_existing_release,
    main, make_plan, prepare_assets, release_versions, write_outputs)


class ReleasePlanTests(unittest.TestCase):
    def root(self, windows='2.6.0', android='2.5.0', code=20500):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / 'version.json').write_text(json.dumps({'version': windows}), encoding='utf-8')
        (root / 'pyproject.toml').write_text('[project]\nversion = "' + windows + '"\n', encoding='utf-8')
        (root / 'gemini_gui.py').write_text('CURRENT_VERSION = "' + windows + '"\n', encoding='utf-8')
        gradle = root / 'apps/android/app/build.gradle.kts'
        gradle.parent.mkdir(parents=True)
        gradle.write_text('android {\n defaultConfig {\n  versionName = "' + android + '"\n'
                          + '  versionCode = ' + str(code) + '\n }\n}\n', encoding='utf-8')
        notes = root / 'docs/releases' / (windows + '.md')
        notes.parent.mkdir(parents=True)
        notes.write_text('Release fixture', encoding='utf-8')
        return root

    @staticmethod
    def release(*, draft=False):
        return {'tag_name': 'v2.6.0', 'draft': draft,
                'published_at': None if draft else '2026-11-01T08:00:00Z'}

    def artifact(self, root, platform, filename, content):
        directory = root / 'release' / platform
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        (directory / 'SHA256SUMS.txt').write_text(digest + '  ' + filename + '\n', encoding='ascii')
        return digest

    def test_windows_only_release_keeps_actual_android_version(self):
        root = self.root()
        plan = make_plan(root, 'v2.6.0')
        self.assertEqual((plan['windows_version'], plan['android_version'], plan['android_code']),
                         ('2.6.0', '2.5.0', 20500))
        self.assertTrue(plan['build_windows'])
        self.assertTrue(plan['publish_allowed'])
        self.assertFalse(plan['include_android'])
        self.assertFalse(plan['build_android'])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            main(['android-version', '--root', str(root)])
        self.assertEqual(output.getvalue(), '2.5.0\n')

    def test_combined_release_builds_both_matching_platforms(self):
        root = self.root(android='2.6.0', code=20600)
        plan = make_plan(root, 'v2.6.0')
        self.assertTrue(plan['build_windows'])
        self.assertTrue(plan['build_android'])
        self.assertTrue(plan['include_android'])

    def test_public_or_draft_release_prevents_all_builds_and_publication(self):
        root = self.root(android='2.6.0', code=20600)
        for draft in (False, True):
            with self.subTest(draft=draft):
                plan = make_plan(root, 'v2.6.0', self.release(draft=draft))
                self.assertEqual(plan['existing_release'], 'draft' if draft else 'published')
                for key in ('build_windows', 'build_android', 'publish_allowed'):
                    self.assertFalse(plan[key])

    def test_release_appearing_during_build_is_left_untouched(self):
        root = self.root()
        self.assertTrue(make_plan(root, 'v2.6.0')['publish_allowed'])
        self.assertFalse(make_plan(root, 'v2.6.0', self.release())['publish_allowed'])
        self.assertFalse(make_plan(root, 'v2.6.0', self.release(draft=True))['publish_allowed'])

    def test_bad_tag_metadata_or_version_code_blocks_release(self):
        root = self.root()
        for tag in ('v2.5.0', '2.6.0', 'v2.6.0-beta', '../v2.6.0'):
            with self.assertRaises(ValueError):
                make_plan(root, tag)
        for record in ({'tag_name': 'v2.6.0', 'draft': 'false'},
                       dict(self.release(), tag_name='v2.5.0'),
                       dict(self.release(), published_at=None)):
            with self.assertRaises(ValueError):
                make_plan(root, 'v2.6.0', record)
        bad_android = self.root(android='2.5.0', code=20600)
        with self.assertRaises(ValueError):
            release_versions(bad_android)
        (root / 'version.json').write_text('{"version":"2.5.0"}', encoding='utf-8')
        with self.assertRaises(ValueError):
            make_plan(root, 'v2.6.0')

    def test_missing_release_notes_block_before_expensive_builds(self):
        root = self.root()
        (root / 'docs/releases/2.6.0.md').unlink()
        with self.assertRaisesRegex(ValueError, 'notes'):
            make_plan(root, 'v2.6.0')

    def test_android_metadata_rejects_ambiguous_or_invalid_declarations(self):
        root = self.root()
        gradle = root / 'apps/android/app/build.gradle.kts'
        for text in ('versionName = "2.5.0"\nversionCode = 20500\nversionCode = 20600\n',
                     'versionName = "2.5.0-beta"\nversionCode = 20500\n',
                     'versionName = "2.5.0"\nversionCode = 1\n'):
            gradle.write_text(text, encoding='utf-8')
            with self.assertRaises(ValueError):
                android_version_info(root)

    def test_windows_only_assets_have_only_exe_checksum(self):
        root = self.root()
        digest = self.artifact(root, 'windows', 'ASIC_Monitor.exe', b'fixture windows binary')
        # A stale or separately built APK must never be pulled into Windows-only publication.
        self.artifact(root, 'android', 'ASIC_Monitor-android-2.5.0-test.apk', b'older Android binary')
        names = prepare_assets(root / 'release', make_plan(root, 'v2.6.0'))
        self.assertEqual(names, ['ASIC_Monitor.exe', 'SHA256SUMS.txt'])
        self.assertEqual((root / 'release/SHA256SUMS.txt').read_text(), digest + '  ASIC_Monitor.exe\n')
        self.assertEqual(list((root / 'release').glob('*.apk')), [])

    def test_combined_assets_verify_both_and_checksum_actual_apk_name(self):
        root = self.root(android='2.6.0', code=20600)
        windows = self.artifact(root, 'windows', 'ASIC_Monitor.exe', b'windows binary')
        filename = 'ASIC_Monitor-android-2.6.0-test.apk'
        android = self.artifact(root, 'android', filename, b'Android binary')
        names = prepare_assets(root / 'release', make_plan(root, 'v2.6.0'))
        self.assertEqual(names, ['ASIC_Monitor.exe', filename, 'SHA256SUMS.txt'])
        self.assertEqual((root / 'release/SHA256SUMS.txt').read_text().splitlines(),
                         [windows + '  ASIC_Monitor.exe', android + '  ' + filename])

    def test_missing_or_corrupt_apk_cannot_publish_partial_combined_release(self):
        for corrupt in (False, True):
            root = self.root(android='2.6.0', code=20600)
            self.artifact(root, 'windows', 'ASIC_Monitor.exe', b'windows binary')
            if corrupt:
                self.artifact(root, 'android', 'ASIC_Monitor-android-2.6.0-test.apk', b'original APK')
                (root / 'release/android/ASIC_Monitor-android-2.6.0-test.apk').write_bytes(b'corrupt APK')
            with self.assertRaises(ValueError):
                prepare_assets(root / 'release', make_plan(root, 'v2.6.0'))
            self.assertFalse((root / 'release/ASIC_Monitor.exe').exists())
            self.assertFalse((root / 'release/SHA256SUMS.txt').exists())

    def test_checksum_cannot_refer_to_other_paths_or_extra_assets(self):
        root = self.root()
        digest = self.artifact(root, 'windows', 'ASIC_Monitor.exe', b'windows binary')
        for text in (digest + '  ../ASIC_Monitor.exe\n',
                     digest + '  ASIC_Monitor.exe\n' + digest + '  extra.exe\n'):
            (root / 'release/windows/SHA256SUMS.txt').write_text(text, encoding='ascii')
            with self.assertRaises(ValueError):
                prepare_assets(root / 'release', make_plan(root, 'v2.6.0'))

    def test_workflow_outputs_are_explicit_lowercase_booleans(self):
        root = self.root()
        output = root / 'job-output'
        write_outputs(output, make_plan(root, 'v2.6.0'))
        self.assertIn('build_windows=true\n', output.read_text())
        self.assertIn('build_android=false\n', output.read_text())
        self.assertIn('android_version=2.5.0\n', output.read_text())


class ReleaseLookupTests(unittest.TestCase):
    def test_exact_metadata_endpoint_uses_scoped_token_and_validates_release(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = json.dumps(ReleasePlanTests.release()).encode()
        result = fetch_existing_release('Drubic8/AgentScanner', 'v2.6.0', 'fixture-token', opener=opener)
        self.assertEqual(result['tag_name'], 'v2.6.0')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/Drubic8/AgentScanner/releases/tags/v2.6.0')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-token')
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 20)

    def test_only_404_is_absent_other_lookup_failures_stop_builds(self):
        for code in (404, 403, 429, 302, 500):
            opener = MagicMock()
            opener.open.side_effect = urllib.error.HTTPError('https://api.github.com/', code, 'fixture', {}, None)
            if code == 404:
                self.assertIsNone(fetch_existing_release('Drubic8/AgentScanner', 'v2.6.0', '', opener=opener))
            else:
                with self.assertRaises(RuntimeError):
                    fetch_existing_release('Drubic8/AgentScanner', 'v2.6.0', 'fixture-secret', opener=opener)

    def test_lookup_rejects_arbitrary_urls_and_unsafe_metadata(self):
        opener = MagicMock()
        for repository in ('https://evil.example/repo', '../repo', 'owner/repo/other', None):
            with self.assertRaises(ValueError):
                fetch_existing_release(repository, 'v2.6.0', 'fixture-token', opener=opener)
        opener.open.assert_not_called()
        self.assertIsNone(NoRedirects().redirect_request(None, None, 302, '', {}, 'https://evil.example'))
        response = opener.open.return_value.__enter__.return_value
        for raw in (b'<html>error</html>', b'[]', b'x' * (1024 * 1024 + 1)):
            response.read.return_value = raw
            with self.assertRaises(ValueError):
                fetch_existing_release('Drubic8/AgentScanner', 'v2.6.0', '', opener=opener)


if __name__ == '__main__':
    unittest.main()
