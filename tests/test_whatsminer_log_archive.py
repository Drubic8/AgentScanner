"""Synthetic archive tests; no ASIC access and no extraction to filesystem."""
import gzip
import io
import tarfile
import unittest
from unittest.mock import patch

from miner_scanner.runtime import Cancelled, Operation, ProtocolError, ScanOptions
import miner_scanner.whatsminer_log_archive as module


def archive(entries, *, format=tarfile.USTAR_FORMAT):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=format) as stream:
        for name, body, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(body) if kind == tarfile.REGTYPE else 0
            info.mtime = 0
            info.linkname = '/private/secret' if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ''
            stream.addfile(info, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
    return gzip.compress(raw.getvalue(), mtime=0)


def file(name, body=b'chain 2 failed\n'):
    return name, body, tarfile.REGTYPE


class WhatsminerArchiveTests(unittest.TestCase):
    def extract(self, data, kind='miner', operation=None):
        return module.extract_whatsminer_log(data, kind,
            operation=operation or Operation(ScanOptions(device_timeout=20)))

    def test_current_files_only_ignore_configs_history_and_device_prefix(self):
        data = archive([
            ('192.0.2.1.logs/', b'', tarfile.DIRTYPE),
            file('192.0.2.1.logs/miner-state.log', b'chain 1 OK\nchain 2 failed\n'),
            file('192.0.2.1.logs/system.log', b'kernel error\n'),
            file('192.0.2.1.logs/pools.log', b'private pool password'),
            file('192.0.2.1.logs/long-miner-state.log', b'old record'),
            file('192.0.2.1.logs/miner-state_history.log.gz', b'binary history'),
            file('192.0.2.1.logs/boot_cfg', b'private cfg'),
        ])
        self.assertEqual(self.extract(data), ('chain 1 OK\nchain 2 failed\n', False))
        self.assertEqual(self.extract(data, 'system'), ('kernel error\n', False))

    def test_empty_is_successful_but_missing_or_nested_log_is_not(self):
        self.assertEqual(self.extract(archive([file('miner-state.log', b'')])), ('', False))
        for entries in ([file('other.log')], [file('unreviewed/miner-state.log')],
                        [file('192.0.2.1.logs/nested/miner-state.log')]):
            with self.assertRaises(module.MissingLogError):
                self.extract(archive(entries))
        for kind in ('config', [], None):
            with self.assertRaises(module.MissingLogError):
                self.extract(archive([file('miner-state.log')]), kind)

    def test_tail_preserves_newest_complete_lines_and_marks_truncation(self):
        body = b'old line\n' * 12 + b'LAST fan failure\n'
        with patch.object(module, 'MAX_TEXT_BYTES', 40):
            text, truncated = self.extract(archive([file('miner-state.log', body)]))
        self.assertTrue(truncated)
        self.assertTrue(text.endswith('LAST fan failure\n'))
        self.assertFalse(text.startswith('ine'))
        self.assertLessEqual(len(text.encode('utf-8')), 40)

    def test_unicode_tail_and_ansi_remain_readable_and_bounded(self):
        body = ('старое\n' * 30 + '\x1b[31mОшибка вентилятора\x1b[0m\r\n').encode('utf-8')
        with patch.object(module, 'MAX_TEXT_BYTES', 80):
            text, truncated = self.extract(archive([file('miner-state.log', body)]))
        self.assertTrue(truncated)
        self.assertIn('Ошибка вентилятора', text)
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\r', text)
        self.assertLessEqual(len(text.encode('utf-8')), 80)

    def test_unsafe_paths_rejected_without_extraction(self):
        for name in ('../miner-state.log', '/miner-state.log', 'x/../miner-state.log',
                     'C:/miner-state.log', 'x\\miner-state.log'):
            with self.subTest(name=name), self.assertRaises(ProtocolError):
                self.extract(archive([file(name)]))

    def test_links_devices_fifo_and_unrequested_files_never_supply_log_text(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind), self.assertRaises(module.MissingLogError):
                self.extract(archive([('miner-state.log', b'', kind)]))
        self.assertEqual(self.extract(archive([
            ('unrelated-link', b'', tarfile.SYMTYPE), file('miner-state.log', b'normal\n')])), ('normal\n', False))

    def test_duplicate_eligible_logs_are_ambiguous(self):
        for entries in ([file('miner-state.log'), file('miner-state.log')],
                        [file('a.logs/miner-state.log'), file('b.logs/miner-state.log')]):
            with self.assertRaises(ProtocolError):
                self.extract(archive(entries))

    def test_expansion_and_compressed_limits_stop_archive_bombs(self):
        data = archive([file('miner-state.log', b'0' * 100_000)])
        with patch.object(module, 'MAX_EXPANDED_BYTES', 16384), self.assertRaises(module.LogArchiveLimitError):
            self.extract(data)
        with patch.object(module, 'MAX_ARCHIVE_BYTES', 10), self.assertRaises(module.LogArchiveLimitError):
            self.extract(data)

    def test_member_limit_includes_unselected_entries(self):
        data = archive([file('unrelated1'), file('unrelated2'), file('miner-state.log')])
        with patch.object(module, 'MAX_MEMBERS', 2), self.assertRaises(module.LogArchiveLimitError):
            self.extract(data)

    def test_pax_gnu_long_metadata_and_sparse_are_rejected(self):
        with self.assertRaises(ProtocolError):
            self.extract(archive([file('a' * 101)], format=tarfile.PAX_FORMAT))
        with self.assertRaises(ProtocolError):
            self.extract(archive([file('a' * 101)], format=tarfile.GNU_FORMAT))
        info = tarfile.TarInfo('miner-state.log')
        info.type = tarfile.GNUTYPE_SPARSE
        raw = info.tobuf(format=tarfile.GNU_FORMAT) + b'\0' * 1024
        with self.assertRaises(ProtocolError):
            self.extract(gzip.compress(raw))

    def test_truncated_gzip_crc_trailing_and_malformed_tar_are_rejected(self):
        good = archive([file('miner-state.log')])
        corrupt = bytearray(good)
        corrupt[-8] ^= 0xff
        for data in (good[:-2], bytes(corrupt), good + b'EXTRA', good + good,
                     gzip.compress(b'not a tar'), gzip.compress(b'X' * 1024)):
            with self.subTest(length=len(data)), self.assertRaises(ProtocolError):
                self.extract(data)
        raw = gzip.decompress(good)
        with self.assertRaises(ProtocolError):
            self.extract(gzip.compress(raw + b'X' * 512))

    def test_declared_member_cannot_extend_past_archive(self):
        info = tarfile.TarInfo('miner-state.log')
        info.size = 1_000_000
        with self.assertRaises(ProtocolError):
            self.extract(gzip.compress(info.tobuf() + b'\0' * 1024))

    def test_binary_current_log_fails_while_unselected_binary_is_ignored(self):
        with self.assertRaises(ProtocolError):
            self.extract(archive([file('miner-state.log', b'\0private')]))
        self.assertEqual(self.extract(archive([file('ut2.zip', b'\0\xff'),
            file('miner-state.log', b'line\xff\n')])), ('line\ufffd\n', False))

    def test_cancel_checked_before_and_during_expansion_and_tar(self):
        data = archive([file('miner-state.log', b'error\n')])
        op = Operation()
        op.cancel.set()
        with self.assertRaises(Cancelled):
            self.extract(data, operation=op)

        class CancelAfter:
            def __init__(self, count):
                self.count = count
            def remaining(self):
                self.count -= 1
                if self.count <= 0:
                    raise Cancelled('cancelled')
                return 30

        for count in (4, 7, 10):
            with self.subTest(count=count), self.assertRaises(Cancelled):
                self.extract(data, operation=CancelAfter(count))


if __name__ == '__main__':
    unittest.main()
