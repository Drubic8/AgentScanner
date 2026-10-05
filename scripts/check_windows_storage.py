"""Restart a disposable EXE and remove only its synthetic former AppData files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / 'artifacts' / 'exe-storage'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='portable-storage-') as scratch:
        directory = (Path(scratch) / '\u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 EXE').resolve()
        directory.mkdir()
        target = directory / 'ASIC Monitor.exe'
        shutil.copyfile(ROOT / 'dist' / 'ASIC_Monitor.exe', target)
        legacy = directory / 'former-appdata' / 'ASICMonitor'
        legacy.mkdir(parents=True)
        tree = [{'type': 'folder', 'name': 'Site', 'children': [{'type': 'folder', 'name': 'Sleep',
                 'children': [{'name': 'Rack', 'ranges': ['192.0.2.0/30'], 'enabled': False}]}]}]
        old_settings = dict(export_dir=str(directory / 'old-pdf'), export_csv_dir=str(directory / 'old-xlsx'),
                            language='ru', density='comfortable', check_updates=False)
        for name, value in (('ip_ranges.json', tree), ('app_settings.json', old_settings)):
            (legacy / name).write_text(json.dumps(value), encoding='utf-8')
        (directory / 'ip_ranges.json').write_text('[]', encoding='utf-8')
        os.utime(directory / 'ip_ranges.json', ns=(1_000_000_000, 1_000_000_000))
        env = dict(os.environ, LOCALAPPDATA=str(legacy.parent), QT_QPA_PLATFORM='offscreen',
                   MINER_SCANNER_DATA_DIR=str(directory / 'scanner'))
        env.pop('ASIC_MONITOR_DATA_DIR', None)
        reports = []
        # cwd is intentionally different from the EXE's directory.
        for number in range(2):
            report_dir = directory / f'run-{number}'
            result = subprocess.run([str(target), '--smoke-test', str(report_dir), '--check-portable-storage'],
                                    cwd=ROOT, env=env, timeout=120)
            if result.returncode:
                raise RuntimeError(f'Packaged storage check failed: {report_dir}')
            report = json.loads((report_dir / 'result.json').read_text(encoding='utf-8'))
            assert report['ok'] and report['loaded_ranges'] == tree, report
            reports.append(report)
            if number == 0:
                assert report['loaded_settings'] == {key: old_settings[key] for key in report['loaded_settings']}
                assert list((directory / '.settings-backup').glob('*/ip_ranges.json'))
                # Remove only the two fixture files, not the user's AppData.
                for name in ('ip_ranges.json', 'app_settings.json'):
                    (legacy / name).unlink()
        assert reports[1]['loaded_settings'] == reports[0]['saved_settings']
        assert json.loads((directory / 'ip_ranges.json').read_text(encoding='utf-8')) == tree
        assert (directory / '.asic-monitor-storage.json').is_file()
        (output / 'result.json').write_text(json.dumps(dict(ok=True, version=reports[1]['version'],
            restarts=2, nested_folders=True, export_paths=True, appdata_removed=True, network_used=False)), encoding='utf-8')
        print('Packaged portable storage, migration and restart passed:', reports[1]['version'])


if __name__ == '__main__':
    main()
