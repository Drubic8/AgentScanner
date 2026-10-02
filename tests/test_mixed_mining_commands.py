"""One GUI action dispatches to different protocols without network access."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport, stock
from tests.test_avalon_commands import AvalonTransport
from miner_scanner import stock_compatibility
from tests.test_stock_compatibility import WebTransport
from tests.test_stock_mode_timeout import device
from tests.test_t21_commands import T21Transport
from tests.test_whatsminer_commands import RpcTransport, info

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ('PyQt6', 'pandas', 'fpdf')),
                     'Desktop dependencies not installed')
class MixedMiningCommandsTests(unittest.TestCase):
    def test_sleep_then_wakeup_dispatches_per_device_and_continues_after_unsupported(self):
        from gemini_gui import ActionWorker

        capture = json.loads((Path(__file__).parent / 'fixtures/avalon_1346_fms.json').read_text())
        running = {'STATS': [{'MM Count': 1, 'MM ID0':
            'Ver[1346-110-24041001_08b0955_0196aba] Elapsed[370] '
            'SYSTEMSTATU[Work: In Work, Hash Board: 3 ] SoftOFF[0] '
            'GHSspd[110778.38] PS[0 1199 1391 225 3126 1393 3348]'}]}
        t21 = device(version='Thu Jan 16 11:00:25 CST 2025')
        t21['config']['bitmain-user-ip-cat'] = '0'
        l9 = device(model='Antminer L9', version='compatible synthetic build')
        l9['config'].pop('bitmain-hashrate-percent')
        l9['config']['bitmain-freq-level'] = '100'
        # Exercise actual read-only interface discovery, including an arbitrary
        # firmware date, using synthetic bytes for the audited L9 contract.
        catalog = deepcopy(stock_compatibility.interfaces())
        contract = next(item for item in catalog['miner'].values() if item['source_asset'] == 'miner.0ef2dc.js')
        bundle = b'/* offline L9 interface fixture */'
        catalog['miner'][hashlib.sha256(bundle).hexdigest()] = contract
        interface_patch = patch.object(stock_compatibility, 'interfaces', return_value=catalog)
        interface_patch.start()
        self.addCleanup(interface_patch.stop)
        devices = {
            # The unsupported entry is deliberately first in the batch.
            '192.0.2.1': (FakeFactory(stock()), FakeTransport),
            '192.0.2.9': (FakeFactory(l9), WebTransport),
            '192.0.2.21': (FakeFactory(t21), T21Transport),
            '192.0.2.61': (FakeFactory({'rpc_info': info()}), RpcTransport),
            '192.0.2.134': (FakeFactory({'version': capture['version'], 'stats': running}), AvalonTransport),
        }
        l9_factory, t21_factory = devices['192.0.2.9'][0], devices['192.0.2.21'][0]
        l9_factory.apply_mode = t21_factory.apply_mode = False
        l9_factory.web = {'/miner.html': b'<script src="/js/miner.0ef2dc.js"></script>',
                          '/js/miner.0ef2dc.js': bundle}
        rpc = devices['192.0.2.61'][0]
        rpc.ports, rpc.code, rpc.desc, rpc.prewrite_change = [], 0, None, None
        avalon = devices['192.0.2.134'][0]
        avalon.led, avalon.bad_read = 0, False

        def transport_factory(ip, *args):
            factory, transport = devices[ip]
            return transport(factory, ip, *args)

        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=transport_factory)
        service.set_default_credentials(Credentials('root', 'root'))
        service.set_credentials('192.0.2.61', Credentials('super', 'fixture-password'))
        rows = [service.poll(ip).to_legacy() for ip in devices]
        baseline_pools = {ip: deepcopy(factory.data.get('config', {}).get('pools'))
                          for ip, (factory, _) in devices.items()}

        with patch('gemini_gui.default_service', return_value=service), \
             patch.object(Operation, 'pause', lambda *_: None), \
             patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network access')):
            for action in ('sleep', 'wakeup'):
                with self.subTest(action=action):
                    avalon.power_after = capture['after_sleep'] if action == 'sleep' else running
                    logs, refreshed = [], []
                    worker = ActionWorker(rows, action)
                    worker.log_signal.connect(logs.append)
                    worker.result_signal.connect(refreshed.extend)
                    worker.run()
                    self.assertEqual(len(logs), len(devices))
                    self.assertEqual(len(refreshed), len(devices))
                    self.assertTrue(any('192.0.2.1: [unsupported]' in line for line in logs))
                    for ip in list(devices)[1:]:
                        self.assertTrue(any(f'{ip}: [succeeded]' in line for line in logs), logs)
                        self.assertEqual(len(devices[ip][0].writes), 1)
                    self.assertFalse(devices['192.0.2.1'][0].writes)
                    for factory in (l9_factory, t21_factory):
                        path, method, payload = factory.writes[0]
                        self.assertEqual((path, method), ('/cgi-bin/set_miner_conf.cgi', 'POST'))
                        self.assertEqual(payload['miner-mode'], 1 if action == 'sleep' else 0)
                    self.assertEqual(rpc.writes[0]['cmd'], 'set.miner.service')
                    self.assertEqual(rpc.writes[0]['param'], 'stop' if action == 'sleep' else 'start')
                    self.assertEqual(avalon.writes, [capture[action]['request']])
                    for ip in ('192.0.2.9', '192.0.2.21'):
                        self.assertEqual(devices[ip][0].writes[0][2]['pools'], baseline_pools[ip])
                    rows = refreshed
                    for factory, _ in devices.values():
                        factory.writes.clear()
