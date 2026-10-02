"""Command certification is limited to the tested S19 system image."""
from copy import deepcopy
import unittest

from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.profiles import ProfileRegistry
from miner_scanner.repository import DeviceRepository
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, stock


def s19():
    data = stock()
    data['version']['VERSION'][0] = {'Type': 'Antminer S19', 'Miner': '49.0.1.3',
                                   'API': '3.1', 'CompileTime': 'Tue Dec  6 16:10:00 CST 2022'}
    data['stats']['STATS'][0]['Type'] = 'Antminer S19'
    data['system'] = {'minertype': 'Antminer S19',
                      'system_filesystem_version': 'Tue Dec 6 16:10:00 CST 2022'}
    return data


class S19CommandTests(unittest.TestCase):
    def test_exact_system_build_required_not_just_miner_number(self):
        registry = ProfileRegistry()
        data = s19()
        self.assertEqual(registry.resolve(data).id, 'bitmain.stock.s19.20221206')
        for changed in ('other build', None):
            other = deepcopy(data)
            other['system']['system_filesystem_version'] = changed
            self.assertEqual(registry.resolve(other).id, 'bitmain.stock')
        other = deepcopy(data)
        other['version']['VERSION'][0]['API'] = 'other-api'
        self.assertEqual(registry.resolve(other).id, 'bitmain.stock')
        other = deepcopy(data)
        other['version']['VERSION'][0]['Type'] = 'Antminer S19 Pro'
        self.assertEqual(registry.resolve(other).id, 'bitmain.stock')

    def test_reboot_works_without_experimental_flag_and_never_replays(self):
        factory = FakeFactory(s19())
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        service.set_default_credentials(Credentials('root', 'root'))
        record = service.poll('192.0.2.19')
        self.assertEqual(record.identity.profile_id, 'bitmain.stock.s19.20221206')
        result = execute_command(service, '192.0.2.19', 'reboot',
                                 device_id=record.identity.device_id, command_id='s19-reboot')
        self.assertTrue(result.accepted_by_api)
        self.assertEqual(result.status, 'unconfirmed')
        self.assertEqual(factory.writes, [('/cgi-bin/reboot.cgi', 'GET', None)])
        again = execute_command(service, '192.0.2.19', 'reboot', command_id='s19-reboot')
        self.assertEqual(again, result)
        self.assertEqual(len(factory.writes), 1)

    def test_other_build_and_untested_actions_remain_blocked(self):
        factory = FakeFactory(s19())
        repository = DeviceRepository(':memory:')
        self.addCleanup(repository.close)
        service = ScannerService(repository=repository, transport_factory=factory)
        service.set_default_credentials(Credentials('root', 'root'))
        service.poll('192.0.2.19')
        self.assertEqual(execute_command(service, '192.0.2.19', 'mining_stop').status, 'unsupported')
        factory.data['system']['system_filesystem_version'] = 'another build'
        service.poll('192.0.2.19', force_identify=True)
        self.assertEqual(execute_command(service, '192.0.2.19', 'reboot').status, 'unsupported')
        self.assertFalse(factory.writes)
