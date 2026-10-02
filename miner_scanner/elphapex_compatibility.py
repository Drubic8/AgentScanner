"""Recognize the DG1/DG1+ LuCI LED API by model family and readback schema."""
from functools import lru_cache
from importlib.resources import files
import json

import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError

LED_ACTIONS = {'identify_on', 'identify_off'}
CORE_ACTIONS = LED_ACTIONS | {'reboot', 'mining_start', 'mining_stop'}


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


def resolve(record, evidence, config=None):
    result = {'rules': {}, 'blocked': set(), 'evidence': dict(evidence)}
    if record.identity.make != 'Elphapex' or record.identity.firmware != 'Stock':
        return result
    result['blocked'].update(CORE_ACTIONS)
    result['evidence']['reported_api_version'] = record.identity.api_version
    if 'blink_boolean' in evidence:
        result['blocked'].update(LED_ACTIONS)
    definition = interfaces()['protocols']['luci-ftm']
    model = record.identity.model.removeprefix('Elphapex ').strip()
    if (definition and model in definition['models'] and definition['led_api'] == 'luci-ftm'
            and evidence.get('blink_boolean') is True):
        # Existing executor, hardware-tested separately; no inferred power modes.
        result['rules'] = {action: None for action in LED_ACTIONS | {'reboot'}}
        if (isinstance(config, dict) and type(config.get('fc-work-mode')) in (str, int)
                and str(config['fc-work-mode']) in {'0', '-1000'}):
            # LuCI's dedicated mode setter updates no pools/fan/tuning fields.
            result['rules'].update(dict.fromkeys(('mining_start', 'mining_stop')))
        result['blocked'].difference_update(result['rules'])
        result['evidence']['led_api'] = 'elphapex.luci.ftm'
    return result
