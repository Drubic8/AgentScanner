"""Read-only recognition of reviewed VNish locate-miner UI/API contracts."""
from functools import lru_cache
import hashlib
from html.parser import HTMLParser
from importlib.resources import files
import json
import re

import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError

LED_ACTIONS = {'identify_on', 'identify_off'}
ASSET_PATH = re.compile(r'/assets/index-[a-zA-Z0-9_-]{1,64}\.js')


@lru_cache(maxsize=1)
def interfaces():
    return json.loads(files('miner_scanner.profiles').joinpath('vnish_interfaces.json').read_text(encoding='utf-8'))


class ScriptPaths(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        source = dict(attrs).get('src', '')
        if tag == 'script' and ASSET_PATH.fullmatch(source):
            self.paths.append(source)


def probe(transport):
    """At most three GETs; never execute JavaScript or follow arbitrary links."""
    evidence = {}
    try:
        status, body = transport.http('/index.html')
        if status != 200:
            return evidence
        evidence['index'] = 'unrecognized'
        parser = ScriptPaths()
        parser.feed(body.decode('utf-8'))
        if len(parser.paths) != 1:
            return evidence
        status, body = transport.http(parser.paths[0])
        if status != 200:
            return evidence
        evidence['index'] = hashlib.sha256(body).hexdigest()
        if evidence['index'] in interfaces()['index']:
            status = transport.http_json('/api/v1/status')
            evidence['find_boolean'] = isinstance(status, dict) and type(status.get('find_miner')) is bool
    except (OSError, requests.RequestException, AuthenticationError, DeadlineExceeded, ProtocolError, ValueError):
        pass
    return evidence


def resolve(info, evidence):
    result = {'rules': {}, 'blocked': set(), 'evidence': dict(evidence)}
    if not isinstance(info, dict) or str(info.get('fw_name', '')).casefold() != 'vnish':
        return result
    if not isinstance(info.get('miner'), str) or not info['miner'].strip():
        return result
    definition = interfaces()['index'].get(evidence.get('index'))
    if 'index' in evidence:
        result['blocked'].update(LED_ACTIONS)
    if definition and definition['led_api'] == 'locate-miner' and evidence.get('find_boolean') is True:
        result['rules'] = {action: {'_vnish_interface': 'locate-miner'} for action in LED_ACTIONS}
        result['blocked'].difference_update(LED_ACTIONS)
    return result
