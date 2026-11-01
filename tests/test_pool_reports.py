"""Three configured pool slots stay searchable and survive real report round trips."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest

from desktop_ui.device_filters import matches_device


EXPORTS_AVAILABLE = all(importlib.util.find_spec(name) for name in ('pandas', 'openpyxl'))
if EXPORTS_AVAILABLE:
    import openpyxl
    import pandas as pd
    from desktop_ui.preferences import COLUMNS
    from desktop_ui.reports import export_frame, sorted_frame, write_spreadsheet


POOL_COLUMNS = ('Pool', 'Worker', 'Pool2', 'Worker2', 'Pool3', 'Worker3')


class PoolSearchTests(unittest.TestCase):
    def test_searches_every_slot_case_insensitively_and_combines_other_filters(self):
        record = dict(IP='192.0.2.1', Model='Demo', Status='Running',
                      Pool='stratum+tcp://primary.invalid:3333', Worker='owner.primary',
                      Pool2='stratum+tcp://backup.invalid:4444', Worker2='Owner.Reserve',
                      Pool3='stratum+tcp://third.invalid:5555', Worker3='owner.emergency')
        for value in ('primary.invalid', 'owner.primary', 'backup.invalid',
                      'OWNER.RESERVE', 'third.invalid', 'OWNER.EMERGENCY'):
            with self.subTest(value=value):
                self.assertTrue(matches_device(record, query=value, status='Running'))
                self.assertFalse(matches_device(record, query=value, status='Sleep'))
        self.assertTrue(matches_device(record, query='demo backup.invalid owner.emergency'))
        self.assertFalse(matches_device(record, query='nonexistent.invalid'))

    def test_missing_backup_slots_do_not_match_none_or_python_representations(self):
        record = dict(IP='192.0.2.2', Pool=None, Worker2=None)
        self.assertTrue(matches_device(record, query='192.0.2.2'))
        self.assertFalse(matches_device(record, query='none'))


@unittest.skipUnless(EXPORTS_AVAILABLE, 'Desktop report dependencies not installed')
class PoolReportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def path(self, suffix):
        return Path(self.directory.name) / ('three-pools.' + suffix)

    def test_selected_columns_have_distinct_labels_and_only_flattened_pool_values(self):
        record = {field: field + '.value' for field in POOL_COLUMNS}
        record.update(IP='192.0.2.1', Pools=[{'url': 'internal.invalid', 'worker': 'internal'}])
        frame = export_frame([record], list(POOL_COLUMNS), 'IP')
        self.assertEqual(list(frame.columns), [COLUMNS[field] for field in POOL_COLUMNS])
        self.assertEqual(len(set(frame.columns)), 6)
        self.assertEqual(frame.iloc[0].tolist(), [record[field] for field in POOL_COLUMNS])
        self.assertNotIn('Pools', frame.columns)

    def test_sort_by_third_worker_without_exporting_sort_column_is_stable(self):
        records = [dict(IP='192.0.2.11', Worker3='beta'),
                   dict(IP='192.0.2.12', Worker3='Alpha'),
                   dict(IP='192.0.2.13', Worker3='ALPHA')]
        frame = export_frame(records, ['IP'], 'Worker3')
        self.assertEqual(frame['IP'].tolist(), ['192.0.2.12', '192.0.2.13', '192.0.2.11'])
        self.assertEqual(list(frame.columns), ['IP'])

    def test_mixed_legacy_and_new_rows_keep_missing_slots_blank(self):
        records = [dict(IP='192.0.2.1', Worker='legacy'),
                   dict(IP='192.0.2.2', Worker='new', Pool2=None, Worker2=None,
                        Pool3='stratum+tcp://third.invalid:3333', Worker3='third')]
        frame = export_frame(records, list(POOL_COLUMNS), 'IP')
        self.assertEqual(frame.iloc[0].tolist(), ['', 'legacy', '', '', '', ''])
        self.assertEqual(frame.iloc[1].tolist(), ['', 'new', '', '', records[1]['Pool3'], 'third'])
        self.assertFalse(pd.isna(frame).any().any())
        self.assertEqual(sorted_frame(records, 'Worker3')['IP'].tolist(), ['192.0.2.1', '192.0.2.2'])

    def test_csv_and_xlsx_keep_all_slots_long_worker_and_literal_formula_text(self):
        long_worker = 'client.' + 'full-worker-name-' * 80
        record = dict(IP='192.0.2.1', Pool='stratum+tcp://first.invalid:3333', Worker='client.primary',
                      Pool2='stratum+tcp://backup.invalid:4444', Worker2='=HYPERLINK("test")',
                      Pool3='stratum+tcp://third.invalid:5555/path,a', Worker3=long_worker)
        frame = export_frame([record], list(POOL_COLUMNS), 'IP')
        original = frame.copy(deep=True)
        write_spreadsheet(frame, self.path('csv'))
        with self.path('csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0], [COLUMNS[field] for field in POOL_COLUMNS])
        expected = [record[field] for field in POOL_COLUMNS]
        expected[3] = "'" + expected[3]
        self.assertEqual(rows[1], expected)

        write_spreadsheet(frame, self.path('xlsx'))
        workbook = openpyxl.load_workbook(self.path('xlsx'), data_only=False)
        self.addCleanup(workbook.close)
        self.assertEqual([cell.value for cell in workbook.active[1]], rows[0])
        self.assertEqual([cell.value for cell in workbook.active[2]], [record[field] for field in POOL_COLUMNS])
        self.assertTrue(all(cell.data_type == 's' for cell in workbook.active[2]))
        pd.testing.assert_frame_equal(frame, original)


if __name__ == '__main__':
    unittest.main()
