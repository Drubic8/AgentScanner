"""Reference regressions and real RPC error payload shapes; no network."""
import unittest

from miner_scanner.whatsminer_errors import decode_errors, lookup
from miner_scanner.parsers.whatsminer import parse_whatsminer_data


class WhatsminerErrorTests(unittest.TestCase):
    def test_corrected_reference_meanings(self):
        cases = {
            '2310': 'хешрейт', '2320': 'хешрейт',
            '233': 'Температурная защита', '235': 'Температурная защита',
            '249': 'повышенного входного напряжения',
            '320': 'чтения температуры', '350': 'температурная защита',
            '510': 'несовместимый тип', '530': 'не обнаружена',
            '540': 'идентификатора чипа', '701': 'не поддерживает',
            '800': 'контрольной суммы cgminer',
            '0x1000': '310 А более 5 минут', '0x2000': '295 А более 10 минут',
        }
        for code, reason in cases.items():
            with self.subTest(code=code):
                self.assertIn(reason, lookup(code)[0])

    def test_exact_codes_override_board_patterns(self):
        for code, reason in [('309', 'всех датчиков'), ('326', 'жидкостного'),
                             ('329', 'контрольной платы'), ('5079', 'Слишком низкая')]:
            with self.subTest(code=code):
                self.assertIn(reason, lookup(code)[0])
                self.assertNotIn('Плата 9', lookup(code)[0])
        self.assertIn('Плата 2', lookup('5072')[0])

    def test_board_chip_process_and_range_patterns(self):
        self.assertIn('Плата 2', lookup('352')[0])
        for prefix in ('52', '53', '54', '55', '56'):
            with self.subTest(prefix=prefix):
                self.assertIn('Плата 1, код чипа 007', lookup(prefix + '1007')[0])
        self.assertIn('температурная защита', lookup('541007')[0])
        self.assertIn('код чипа 999', lookup('541999')[0])
        self.assertNotIn('всех чипов', lookup('541999')[0])
        self.assertIn('процесса', lookup('9001')[0])
        self.assertIn('регистра', lookup('280')[0])
        self.assertIn('регистра', lookup('299')[0])
        for code in ('279', '3000', '54001', '5410000', '0x4000'):
            self.assertIsNone(lookup(code))

    def test_rpc_maps_arrays_and_objects_preserve_device_reason(self):
        for value in (2310, '2310', {'2310': 1780000000}, [2310],
                      [{'2310': 1780000000}], {'code': 2310}):
            with self.subTest(value=value):
                codes, details = decode_errors(value)
                self.assertEqual(codes, '2310')
                self.assertIn('низкий хешрейт', details)
                self.assertIn('03.06.2026, стр. 3', details)
        codes, details = decode_errors([
            {'code': 2310, 'reason': 'device text'},
            {'2310': 1234, 'reason': 'device text'},
            {'110': 5678, 'reason': 'fan text', 'timestamp': 1234},
        ])
        self.assertEqual(codes, '2310-110')
        self.assertEqual(details.count('device text'), 1)
        self.assertIn('fan text', details)
        self.assertNotIn('1234', details)

    def test_empty_invalid_and_unknown_codes(self):
        self.assertEqual(decode_errors([None, False, True, -1, 0, '0x0000',
                                        {'reason': None}, 'invalid', 2310.5]), ('', ''))
        codes, details = decode_errors({'code': 987654, 'reason': 'new firmware fault'})
        self.assertEqual(codes, '987654')
        self.assertIn('нет расшифровки', details)
        self.assertIn('new firmware fault', details)
        self.assertNotIn('стр.', details)
        self.assertEqual(decode_errors(['002310', 2310])[0], '2310')
        self.assertEqual(decode_errors('0X8')[0], '0x0008')

    def test_parser_attaches_details_for_gui(self):
        for key in ('error-code', 'error_code'):
            row = parse_whatsminer_data('192.0.2.1', {
                'code': 0, 'msg': {'miner': {'type': 'M61', 'working': True},
                                  key: [{'2310': 1780000000, 'reason': 'Low rate'}]},
            }, None, None)
            self.assertEqual(row['Error'], '2310')
            self.assertIn('низкий хешрейт', row['ErrorDetails'])
            self.assertIn('Low rate', row['ErrorDetails'])
            self.assertEqual(row['Status'], 'Running')
