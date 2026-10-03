"""Legacy locate forms are authorized only by the reviewed static page."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from miner_scanner import stock_compatibility as compat
from miner_scanner.commands import execute_command
from miner_scanner.models import Credentials
from miner_scanner.repository import DeviceRepository
from miner_scanner.runtime import Operation
from miner_scanner.service import ScannerService
from tests.fakes import FakeFactory, FakeTransport, stock


PAGE=b'<!-- synthetic legacy startBlink/stopBlink/onPageLoaded interface -->'

class LegacyTransport(FakeTransport):
    def http(self,path,method='GET',*,payload=None,form=None,**kwargs):
        if method=='GET' and path=='/blink.html': return 200,self.factory.page
        if method=='GET' and path in ('/index.html','/miner.html','/reboot.html'): return 404,b''
        if path=='/cgi-bin/blink.cgi':
            assert method=='POST' and payload is None and set(form)=={'action'}
            action=form['action']
            self.factory.calls.append((self.ip,action))
            if action!='onPageLoaded':
                self.factory.writes.append((path,method,deepcopy(form)))
                self.factory.led=action=='startBlink'
                if self.factory.write_timeout:raise TimeoutError()
                if self.factory.http_500:return 500,b'CGI terminated'
            value=self.factory.led if self.factory.state_valid else 'false'
            return 200,json.dumps({'isBlinking':value}).encode()
        return super().http(path,method,payload=payload,**kwargs)

    def http_json(self,path,method='GET',**kwargs):
        if path=='/cgi-bin/blink.cgi':return json.loads(self.http(path,method,**kwargs)[1])
        return super().http_json(path,method,**kwargs)


class LegacyBlinkTests(unittest.TestCase):
    def setUp(self):
        catalog=deepcopy(compat.interfaces())
        catalog['legacy_blink']={hashlib.sha256(PAGE).hexdigest():{'duration_seconds':300}}
        self.addCleanup(patch.stopall)
        patch.object(compat,'interfaces',return_value=catalog).start()
        patch.object(Operation,'pause',lambda *_:None).start()
        data=stock()
        data['system']={'minertype':'Antminer Z15','macaddr':'02:00:00:00:00:15','system_filesystem_version':'test build'}
        self.factory=FakeFactory(data)
        self.factory.page=PAGE;self.factory.led=False;self.factory.state_valid=True;self.factory.write_timeout=False
        self.factory.http_500=False
        repo=DeviceRepository(':memory:');self.addCleanup(repo.close)
        self.service=ScannerService(repository=repo,transport_factory=lambda *args:LegacyTransport(self.factory,*args))
        self.service.set_default_credentials(Credentials('root','fixture-password'))

    def test_scan_reads_state_without_starting_blink_and_refreshes_cached_status(self):
        record=self.service.poll('192.0.2.15')
        self.assertEqual(record.capabilities['identify_on'],'supported')
        self.assertIs(record.telemetry.identify_enabled,False)
        self.assertEqual(record.display['ControlCompatibility']['interfaces']['led_duration_seconds'],300)
        self.factory.led=True
        self.assertIs(self.service.poll('192.0.2.15').telemetry.identify_enabled,True)
        self.factory.led=False # Device timer expires independently of the client.
        self.assertIs(self.service.poll('192.0.2.15').telemetry.identify_enabled,False)
        self.assertFalse(self.factory.writes)
        self.assertTrue(all(action=='onPageLoaded' for ip,action in self.factory.calls if action in ('startBlink','stopBlink','onPageLoaded')))

    def test_start_stop_forms_verified_and_command_id_is_not_replayed(self):
        self.service.poll('192.0.2.15')
        on=execute_command(self.service,'192.0.2.15','led_on',command_id='once')
        self.assertEqual(on.status,'succeeded')
        self.assertEqual(execute_command(self.service,'192.0.2.15','led_on',command_id='once'),on)
        self.assertEqual(execute_command(self.service,'192.0.2.15','led_off').status,'succeeded')
        self.assertEqual([w[2] for w in self.factory.writes],[{'action':'startBlink'},{'action':'stopBlink'}])

    def test_timeout_checks_state_without_repeating_start(self):
        self.service.poll('192.0.2.15')
        self.factory.write_timeout=True
        result=execute_command(self.service,'192.0.2.15','led_on')
        self.assertEqual(result.status,'succeeded')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual(len(self.factory.writes),1)

    def test_changed_page_or_non_boolean_state_blocks_new_commands(self):
        for condition in ('page','state'):
            with self.subTest(condition=condition):
                self.factory.page=PAGE;self.factory.state_valid=True
                self.service.poll('192.0.2.15',force_identify=True)
                if condition=='page':self.factory.page=PAGE+b' changed'
                else:self.factory.state_valid=False
                result=execute_command(self.service,'192.0.2.15','led_on')
                self.assertEqual(result.status,'unsupported')
                self.assertFalse(self.factory.writes)

    def test_unknown_page_never_posts_to_command_endpoint(self):
        self.factory.page=b'unknown'
        self.service.poll('192.0.2.15')
        self.assertFalse(any(action=='onPageLoaded' for _,action in self.factory.calls))
        self.assertFalse(self.factory.writes)

    def test_http_500_stop_requires_false_readback_and_does_not_resend(self):
        self.factory.led=True
        self.service.poll('192.0.2.15')
        self.factory.http_500=True
        result=execute_command(self.service,'192.0.2.15','led_off')
        self.assertEqual(result.status,'succeeded')
        self.assertFalse(result.accepted_by_api)
        self.assertEqual([w[2] for w in self.factory.writes],[{'action':'stopBlink'}])
