"""Recognize the hardware-tested DG1 LuCI API, independent of web-page auth."""
from functools import lru_cache
from importlib.resources import files
import json

import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError

LED_ACTIONS = {'identify_on', 'identify_off'}


@lru_cache(maxsize=1)
def interfaces():
    return json.loads(files('miner_scanner.profiles').joinpath('elphapex_interfaces.json').read_text(encoding='utf-8'))


def probe(transport):
    """One fixed, read-only LuCI request; never probe a control endpoint."""
    evidence = {}
    try:
        blink = transport.http_json('/cgi-bin/luci/get_blink_status.cgi')
        evidence['blink_boolean'] = isinstance(blink, dict) and type(blink.get('blink')) is bool
    except (OSError, requests.RequestException, AuthenticationError, DeadlineExceeded, ProtocolError, ValueError):
        pass
    return evidence


def resolve(record, evidence):
    result = {'rules': {}, 'blocked': set(), 'evidence': dict(evidence)}
    if record.identity.make != 'Elphapex' or record.identity.firmware != 'Stock':
        return result
    result['evidence']['luci_api'] = record.identity.api_version
    if 'blink_boolean' in evidence:
        result['blocked'].update(LED_ACTIONS)
    definition = interfaces()['luci_api'].get(record.identity.api_version)
    model = record.identity.model.removeprefix('Elphapex ').strip()
    if (definition and model in definition['models'] and definition['led_api'] == 'luci-ftm'
            and evidence.get('blink_boolean') is True):
        # Existing executor, hardware-tested separately; no inferred power modes.
        result['rules'] = {action: None for action in LED_ACTIONS}
        result['blocked'].difference_update(LED_ACTIONS)
    return result
