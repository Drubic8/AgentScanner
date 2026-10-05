"""Sidecar persistence and migration; temporary files only, no miner access."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from desktop_ui import storage
from desktop_ui.preferences import defaults, load_json, write_json
REAL_DATA_DIRECTORY = storage.data_directory

TREE = [{'type': 'folder', 'name': 'Site / sleep', 'children': [
    {'type': 'folder', 'name': 'Rack', 'children': [
        {'name': 'Night', 'ranges': ['192.0.2.0/30'], 'enabled': False}]}]}]


class PortableStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / 'portable'
        self.old = self.root / 'AppData' / 'ASICMonitor'
        self.local.mkdir()
        self.old.mkdir(parents=True)
        env = dict(os.environ)
        env.pop('ASIC_MONITOR_DATA_DIR', None)
        self.enterContext(patch.dict(os.environ, env, clear=True))
        self.enterContext(patch.object(storage, 'data_directory', return_value=self.local))
        self.enterContext(patch.object(storage, 'former_data_directory', return_value=self.old))

    def test_actual_exe_directory_and_explicit_override_win_over_cwd_and_meipass(self):
        executable = self.local / 'ASIC Monitor.exe'
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(executable)), patch.object(sys, '_MEIPASS', str(self.root / 'unpacked'), create=True):
            self.assertEqual(REAL_DATA_DIRECTORY(), self.local.resolve())
            with patch.dict(os.environ, ASIC_MONITOR_DATA_DIR=str(self.root / 'override')):
                self.assertEqual(REAL_DATA_DIRECTORY(), self.root / 'override')

    def test_newer_appdata_tree_and_paths_migrate_once_with_sidecar_backup(self):
        name = 'ip_ranges.json'
        write_json(self.local / name, [{'name': 'Old', 'ranges': ['198.51.100.1']}])
        os.utime(self.local / name, ns=(1_000_000_000, 1_000_000_000))
        write_json(self.old / name, TREE)
        settings = dict(defaults(), export_dir='D:/Reports/pdf', export_csv_dir='D:/Reports/xlsx')
        write_json(self.old / 'app_settings.json', settings)
        encrypted = b'ASIC-ACCESS-1\nopaque-encrypted-fixture'
        (self.old / 'access_profiles.dat').write_bytes(encrypted)
        storage.initialize_storage()
        self.assertEqual(load_json(self.local / name), TREE)
        self.assertEqual(load_json(self.local / 'app_settings.json'), settings)
        self.assertEqual((self.local / 'access_profiles.dat').read_bytes(), encrypted)
        backups = list((self.local / '.settings-backup').glob('*/ip_ranges.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(load_json(backups[0])[0]['name'], 'Old')
        self.assertEqual(load_json(self.old / name), TREE)
        write_json(self.old / name, [])
        storage.initialize_storage()
        self.assertEqual(load_json(self.local / name), TREE)

    def test_newer_sidecar_wins_and_missing_appdata_does_not_reset_settings(self):
        write_json(self.old / 'ip_ranges.json', [])
        os.utime(self.old / 'ip_ranges.json', ns=(1_000_000_000, 1_000_000_000))
        write_json(self.local / 'ip_ranges.json', TREE)
        storage.initialize_storage()
        (self.old / 'ip_ranges.json').unlink()
        storage.initialize_storage()
        self.assertEqual(load_json(self.local / 'ip_ranges.json'), TREE)

    def test_invalid_newer_json_is_not_selected_over_valid_sidecar(self):
        write_json(self.local / 'ip_ranges.json', TREE)
        (self.old / 'ip_ranges.json').write_text('{broken', encoding='utf-8')
        storage.initialize_storage()
        self.assertEqual(load_json(self.local / 'ip_ranges.json'), TREE)

    def test_corrupt_sidecar_is_preserved_before_recovery_and_invalid_only_file_fails(self):
        (self.local / 'ip_ranges.json').write_bytes(b'broken')
        write_json(self.old / 'ip_ranges.json', TREE)
        storage.initialize_storage()
        backup = next((self.local / '.settings-backup').glob('*/ip_ranges.json'))
        self.assertEqual(backup.read_bytes(), b'broken')
        (self.local / storage.MARKER).unlink()
        (self.local / 'app_settings.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            storage.initialize_storage()
        self.assertFalse((self.local / storage.MARKER).exists())

    def test_explicit_directory_does_not_import_personal_settings(self):
        write_json(self.old / 'ip_ranges.json', TREE)
        with patch.dict(os.environ, ASIC_MONITOR_DATA_DIR=str(self.local)):
            storage.initialize_storage()
        self.assertFalse((self.local / 'ip_ranges.json').exists())
        self.assertFalse((self.local / storage.MARKER).exists())

    def test_write_failure_keeps_original_and_cleans_temporary_file(self):
        path = self.local / 'app_settings.json'
        write_json(path, {'theme': 'dark'})
        with patch.object(storage.os, 'replace', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                write_json(path, {'theme': 'light'})
        self.assertEqual(load_json(path), {'theme': 'dark'})
        self.assertEqual(list(self.local.iterdir()), [path])

    def test_unwritable_directory_fails_before_import_and_never_falls_back(self):
        write_json(self.old / 'ip_ranges.json', TREE)
        with patch.object(storage.tempfile, 'TemporaryFile', side_effect=PermissionError('read-only')):
            with self.assertRaises(PermissionError):
                storage.initialize_storage()
        self.assertEqual(list(self.local.iterdir()), [])

    def test_two_process_launches_keep_tree_and_export_paths_after_appdata_changes(self):
        write_json(self.old / 'ip_ranges.json', TREE)
        write_json(self.old / 'app_settings.json', {'export_dir': 'D:/pdf', 'export_csv_dir': 'D:/xlsx'})
        code = "from desktop_ui import storage; from pathlib import Path; import sys; storage.data_directory=lambda:Path(sys.argv[1]); storage.former_data_directory=lambda:Path(sys.argv[2]); storage.initialize_storage()"
        env = dict(os.environ)
        env.pop('ASIC_MONITOR_DATA_DIR', None)
        root = Path(__file__).resolve().parents[1]
        for number in range(2):
            subprocess.run([sys.executable, '-c', code, str(self.local), str(self.old)], cwd=root, env=env, check=True, timeout=20)
            self.assertEqual(load_json(self.local / 'ip_ranges.json'), TREE)
            self.assertEqual(load_json(self.local / 'app_settings.json')['export_csv_dir'], 'D:/xlsx')
            for name in ('ip_ranges.json', 'app_settings.json'):
                (self.old / name).unlink(missing_ok=True)


@unittest.skipUnless(importlib.util.find_spec('PyQt6') and importlib.util.find_spec('pandas') and importlib.util.find_spec('fpdf'), 'Desktop dependencies not installed')
class PortableGuiPersistenceTests(unittest.TestCase):
    def test_gui_saves_and_reloads_nested_networks_and_report_paths(self):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        import gemini_gui as gui
        app = gui.QApplication.instance() or gui.QApplication([])
        with tempfile.TemporaryDirectory() as scratch:
            directory = Path(scratch)
            with patch.object(gui, 'CONFIG_FILE', directory / 'ip_ranges.json'), patch.object(gui, 'SETTINGS_FILE', directory / 'app_settings.json'):
                settings = dict(defaults(), export_dir='D:/pdf', export_csv_dir='D:/xlsx', language='en', density='compact')
                window = gui.GeminiApp(settings=settings, ranges=[], persist_preferences=True)
                try:
                    self.assertTrue(window.commit_ranges(TREE))
                    window.table.setColumnWidth(0, 123)
                    self.assertTrue(window.persist_ui_preferences())
                finally:
                    window.close()
                window = gui.GeminiApp()
                try:
                    self.assertEqual(window.ranges_config, TREE)
                    self.assertEqual(window.app_settings['export_dir'], 'D:/pdf')
                    self.assertEqual(window.app_settings['export_csv_dir'], 'D:/xlsx')
                    self.assertEqual(window.app_settings['language'], 'en')
                    self.assertEqual(window.app_settings['density'], 'compact')
                    self.assertEqual(window.table.columnWidth(0), 123)
                finally:
                    window.close()
                app.processEvents()
