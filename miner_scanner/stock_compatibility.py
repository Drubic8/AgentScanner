"""Read-only recognition of audited Bitmain web/API contracts, independent of dates.

The shared CGI API is recognized by its readback schema. Reviewed configuration
schemas select basic modes; UI digests establish model-specific power options.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
from html.parser import HTMLParser
from importlib.resources import files
import json
import re

import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError

MODE_ACTIONS = {'mining_stop', 'mining_start', 'normal_power', 'low', 'hem'}
HEAD_ACTIONS = {'identify_on', 'identify_off', 'reboot'}
ASSET_PATH = re.compile(r'/js/(?:miner|index)\.[a-zA-Z0-9_-]{1,64}\.js')


@lru_cache(maxsize=1)
def interfaces():
    return json.loads(files('miner_scanner.profiles').joinpath('stock_interfaces.json').read_text(encoding='utf-8'))


def eligible(record):
    # The CGI header API is shared by Stock/PitBit models, including KS5.
    # Power modes still require an explicit model entry in the miner contract.
    return record.identity.make == 'Bitmain' and record.identity.firmware in {'Stock', 'PitBit'}


def model_name(value):
    return re.sub(r'^Antminer\s+', '', value).strip()


class ScriptPaths(HTMLParser):
    def __init__(self, kind):
        super().__init__()
        self.kind, self.paths = kind, []

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        source = dict(attrs).get('src', '')
        path = '/' + source if source.startswith('js/') else source
        if ASSET_PATH.fullmatch(path) and path.startswith('/js/' + self.kind + '.'):
            self.paths.append(path)


def probe(transport):
    """Bounded read-only API/asset GETs, using the caller's deadline.

    Legacy pages embed reboot code in static HTML instead of an index bundle.
    Only fixed read-only paths are requested; never follow device action links.
    """
    evidence = {}
    # Read this first: a changed/slow UI must not discard a working LED API.
    try:
        blink = transport.http_json('/cgi-bin/get_blink_status.cgi')
        evidence['blink_boolean'] = isinstance(blink, dict) and type(blink.get('blink')) is bool
    except (OSError, requests.RequestException, AuthenticationError, ProtocolError, ValueError, DeadlineExceeded):
        pass
    for kind in ('miner', 'index'):
        try:
            status, body = transport.http('/' + kind + '.html')
            if status != 200:
                continue
            evidence[kind] = 'unrecognized'
            if kind == 'miner':
                # Some KS5 pages omit the selector while shipping JS with the
                # same Normal/Sleep scaffolding. Review HTML and JS together.
                evidence['miner_page'] = hashlib.sha256(body).hexdigest()
            parser = ScriptPaths(kind)
            parser.feed(body.decode('utf-8'))
            if len(parser.paths) != 1:
                if kind == 'index' and not parser.paths:
                    status, body = transport.http('/reboot.html')
                    if status == 200:
                        evidence['reboot_page'] = hashlib.sha256(body).hexdigest()
                continue
            status, body = transport.http(parser.paths[0])
            if status == 200:
                evidence[kind] = hashlib.sha256(body).hexdigest()
        except (OSError, requests.RequestException, AuthenticationError, ProtocolError, ValueError):
            continue
        except DeadlineExceeded:
            break  # Optional discovery must not discard collected telemetry.
    return evidence


def config_mapping(config, contract):
    """Validate the read schema and construct the audited read-to-write mapping."""
    if not isinstance(config, dict):
        return None
    required = contract['required']
    optional = contract.get('optional', {})
    sources = set(required.values()) | set(optional.values()) | {'bitmain-work-mode'}
    if set(config) - sources - set(contract.get('read_only', [])):
        return None
    if any(key not in config or config[key] is None for key in required.values()):
        return None
    mode = config.get('bitmain-work-mode')
    if type(mode) not in (str, int) or not re.fullmatch(r'\d+', str(mode)):
        return None
    pools = config.get('pools')
    if not isinstance(pools, list) or not 1 <= len(pools) <= 3:
        return None
    # Empty pool slots and omitted passwords are returned by stock firmware.
    # Preserve them verbatim, as JSON.stringify does for undefined UI fields.
    if any(not isinstance(p, dict) or set(p) - {'url', 'user', 'pass'}
           or any(not isinstance(value, str) for value in p.values()) for p in pools):
        return None
    for key in sources - {'pools', 'bitmain-work-mode'}:
        if key in config and config[key] is not None and type(config[key]) not in (str, int, float, bool):
            return None
    return {**required, **{target: source for target, source in optional.items()
                          if source in config and config[source] is not None}}


def mode_contract(model, config, evidence):
    """Prefer the reviewed UI; otherwise require unanimous config-to-write maps.

    Matching a read schema can establish the common Sleep/Wakeup ABI, but does
    not establish which optional power modes the current build exposes.
    """
    definition = interfaces()['miner'].get(evidence.get('miner'))
    if definition:
        if model not in definition['modes']:
            return None
        if 'pages' in definition and evidence.get('miner_page') not in definition['pages']:
            return definition, None, {}, 'unconfirmed_ui_page'
        return definition, config_mapping(config, definition), definition['modes'][model], 'reviewed_ui'
    candidates = []
    for definition in interfaces()['miner'].values():
        if not definition.get('config_fallback', True):
            continue  # The read schema alone cannot distinguish these builds.
        modes = definition['modes'].get(model, {})
        if not {'mining_stop', 'mining_start'} <= modes.keys():
            continue
        mapping = config_mapping(config, definition)
        if mapping:
            basic = {action: modes[action] for action in ('mining_stop', 'mining_start')}
            candidates.append((definition, mapping, basic))
    if not candidates:
        return None
    first = candidates[0]
    signature = (first[0]['write_mode'], first[1], first[2])
    if any((definition['write_mode'], mapping, basic) != signature for definition, mapping, basic in candidates[1:]):
        return None  # Never choose one of several conflicting payloads by trial.
    return *first, 'config_schema'


def resolve(record, config, evidence):
    """Return only local rules. Evidence contains no config, pools or passwords."""
    result = {'rules': {}, 'blocked': set(), 'evidence': dict(evidence)}
    if not eligible(record):
        return result
    model = model_name(record.identity.model)
    selected = mode_contract(model, config, evidence) if record.identity.firmware == 'Stock' else None
    if selected:
        definition, mapping, actions, basis = selected
        # A recognized UI is authoritative about absent modes, even if an old
        # exact-build profile had them. A missing schema disables all its modes.
        result['blocked'].update(MODE_ACTIONS)
        if basis == 'config_schema':
            # Preserve documented profile modes, including explicitly opted-in
            # experimental ones. Generic profiles expose no such extra actions.
            # A recognized UI remains authoritative about an absent mode.
            result['blocked'].difference_update(action for action in MODE_ACTIONS
                                                if record.capabilities.get(action) in {'supported', 'unverified'})
        result['evidence']['mode_basis'] = basis
        if mapping:
            for action, target in actions.items():
                current = config['bitmain-work-mode']
                result['rules'][action] = {
                    'path': '/cgi-bin/set_miner_conf.cgi', 'method': 'POST',
                    'read_config': '/cgi-bin/get_miner_conf.cgi', 'config_fields': mapping,
                    'payload': {definition['write_mode']: target},
                    'success_codes': [0, '0', 'B000', 'M000'],
                    'verify': {'path': '/cgi-bin/get_miner_conf.cgi',
                               'field': ['bitmain-work-mode'],
                               'equals': str(target) if type(current) is str else target},
                    '_stock_contract': deepcopy(definition),
                }
                result['blocked'].discard(action)
    elif record.identity.firmware == 'Stock' and ('miner' in evidence or any(
            not definition.get('config_fallback', True) and model in definition['modes']
            for definition in interfaces()['miner'].values())):
        result['blocked'].update(MODE_ACTIONS)
    if evidence.get('index') in interfaces()['index']:
        result['rules']['reboot'] = None  # Existing GET executor, no blind retry.
    elif 'index' in evidence:
        result['blocked'].update(HEAD_ACTIONS)
    # Stock/PitBit's CGI LED setter is independent of the frontend build and
    # model's power modes. Never infer it from HTML strings or firmware dates.
    for action in ('identify_on', 'identify_off'):
        if record.capabilities.get(action) == 'supported':
            # Exact hardware-tested rules still verify the final state if a
            # status read is temporarily absent. This is not a new permission.
            result['blocked'].discard(action)
        else:
            result['blocked'].add(action)
    if evidence.get('blink_boolean') is True:
        result['evidence']['led_api'] = 'bitmain.cgi.blink'
        # The CGI family also defines the standard GET reboot endpoint.
        result['rules']['reboot'] = None
        result['blocked'].discard('reboot')
        for action, enabled in (('identify_on', True), ('identify_off', False)):
            result['rules'][action] = {
                'path': '/cgi-bin/blink.cgi', 'method': 'POST', 'payload': {'blink': enabled},
                'success_codes': [0, '0', 'B000'] if enabled else [0, '0', 'B000', 'B100'],
                'verify': {'path': '/cgi-bin/get_blink_status.cgi', 'field': ['blink'], 'equals': enabled},
            }
            result['blocked'].discard(action)
    if evidence.get('reboot_page') in interfaces().get('legacy_reboot', {}):
        result['rules']['reboot'] = None
        result['blocked'].discard('reboot')
    return result


def apply_capabilities(record, profile, compatibility):
    for action in compatibility['blocked']:
        record.capabilities[action] = 'unsupported'
    for action in compatibility['rules']:
        record.capabilities[action] = 'supported'
    record.display['ControlCompatibility'] = {
        'basis': 'api_contract', 'interfaces': compatibility['evidence'],
        'compatible_commands': sorted(compatibility['rules']),
        'hardware_verified_commands': list(profile.verified_commands),
    }
