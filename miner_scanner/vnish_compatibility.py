"""Discover VNish LED APIs from OpenAPI, with reviewed UI assets as fallback."""
from functools import lru_cache
import hashlib
from html.parser import HTMLParser
from importlib.resources import files
import json
import re

import requests

from .runtime import AuthenticationError, DeadlineExceeded, ProtocolError

LED_ACTIONS = {'identify_on', 'identify_off'}
CORE_PATHS = {'reboot': '/system/reboot', 'mining_stop': '/mining/stop', 'mining_start': '/mining/start'}
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
    """Fixed read-only paths; never execute scripts or probe control endpoints."""
    evidence = {}
    try:
        status = transport.http_json('/api/v1/status')
        evidence['find_boolean'] = isinstance(status, dict) and type(status.get('find_miner')) is bool
        try:
            spec = transport.http_json('/docs/api-doc.json')
        except (OSError, requests.RequestException, AuthenticationError, ProtocolError, ValueError):
            spec = None
        if isinstance(spec, dict) and 'openapi' in spec:
            evidence['led_api'] = openapi_led_api(spec) or 'unrecognized'
            evidence['core_actions'] = openapi_core_actions(spec)
            evidence['discovery'] = 'openapi'
            return evidence
        status, body = transport.http('/index.html')
        if status != 200:
            return evidence
        evidence['index'] = 'unrecognized'
        parser = ScriptPaths()
        parser.feed(body.decode('utf-8'))
        if len(parser.paths) != 1:
            return evidence
        status, body = transport.http(parser.paths[0], response_limit=transport.operation.options.max_asset_bytes)
        if status != 200:
            return evidence
        evidence['index'] = hashlib.sha256(body).hexdigest()
    except (OSError, requests.RequestException, AuthenticationError, DeadlineExceeded, ProtocolError, ValueError):
        pass
    return evidence


def boolean_schema(spec, schema, field, visited=()):
    """Match the documented boolean setter/status shape; resolve local refs only."""
    if not isinstance(schema, dict) or len(visited) >= 8:
        return False
    if '$ref' in schema:
        ref = schema['$ref']
        prefix = '#/components/schemas/'
        if not isinstance(ref, str) or not ref.startswith(prefix) or ref in visited:
            return False
        name = ref[len(prefix):]
        if '/' in name or '~' in name:
            return False
        components = spec.get('components')
        schemas = components.get('schemas') if isinstance(components, dict) else None
        target = schemas.get(name) if isinstance(schemas, dict) else None
        return boolean_schema(spec, target, field, (*visited, ref))
    if 'oneOf' in schema:
        choices = schema['oneOf']
        if not isinstance(choices, list) or not 1 <= len(choices) <= 4:
            return False
        objects = [s for s in choices if s != {'type': 'null'}]
        return len(objects) == 1 and boolean_schema(spec, objects[0], field, (*visited, 'oneOf'))
    properties = schema.get('properties', {})
    return (schema.get('type') == 'object' and schema.get('required') == [field]
            and isinstance(properties, dict) and isinstance(properties.get(field), dict)
            and properties[field].get('type') == 'boolean')


def local_api_spec(spec):
    if not re.fullmatch(r'3\.\d+\.\d+', str(spec.get('openapi', ''))):
        return False
    servers = spec.get('servers', [])
    if (not isinstance(servers, list) or not servers
            or any(not isinstance(s, dict) or s.get('url') not in ('/api/v1', '/api/v1/') for s in servers)):
        return False
    return isinstance(spec.get('paths'), dict)


def openapi_core_actions(spec):
    """Known no-body POST operations; require their current API declaration."""
    if not local_api_spec(spec):
        return []
    result = []
    for action, path in CORE_PATHS.items():
        entry = spec['paths'].get(path, {})
        operation = entry.get('post') if isinstance(entry, dict) else None
        if not isinstance(operation, dict):
            continue
        responses = operation.get('responses', {})
        body = operation.get('requestBody', {})
        if (isinstance(responses, dict) and isinstance(responses.get('200'), dict)
                and isinstance(body, dict) and '$ref' not in body and not body.get('required')):
            result.append(action)
    return sorted(result)


def openapi_led_api(spec):
    """Select a known local setter from its declared POST schema, not a version."""
    if not local_api_spec(spec):
        return None
    paths = spec.get('paths', {})
    if not isinstance(paths, dict):
        return None
    # New VNish declares locate-miner alongside deprecated find-miner. Prefer
    # the newer setter. Both payloads are established local contracts.
    for api, field in (('locate-miner', 'is_enabled'), ('find-miner', 'on')):
        path = paths.get('/' + api, {})
        operation = path.get('post') if isinstance(path, dict) else None
        if not isinstance(operation, dict):
            continue
        try:
            schema = operation['responses']['200']['content']['application/json']['schema']
            if not boolean_schema(spec, schema, field):
                continue
            # Some shipped docs omit requestBody. If present, its shape must
            # agree with the known setter; don't ignore a changed contract.
            if 'requestBody' in operation:
                request = operation['requestBody']['content']['application/json']['schema']
                if not boolean_schema(spec, request, field):
                    continue
        except (KeyError, TypeError, AttributeError):
            continue
        return api
    return None


def resolve(info, evidence):
    result = {'rules': {}, 'blocked': set(), 'evidence': dict(evidence)}
    if not isinstance(info, dict) or str(info.get('fw_name', '')).casefold() != 'vnish':
        return result
    if not isinstance(info.get('miner'), str) or not info['miner'].strip():
        return result
    definition = interfaces()['index'].get(evidence.get('index'))
    api = evidence.get('led_api', definition['led_api'] if definition else None)
    result['blocked'].update(LED_ACTIONS)
    if api in {'find-miner', 'locate-miner'} and evidence.get('find_boolean') is True:
        result['rules'] = {action: {'_vnish_interface': api} for action in LED_ACTIONS}
        result['blocked'].difference_update(LED_ACTIONS)
    if evidence.get('discovery') == 'openapi':
        result['blocked'].update(CORE_PATHS)
        for action in evidence.get('core_actions', []):
            if action in CORE_PATHS:
                result['rules'][action] = None
                result['blocked'].discard(action)
    return result
