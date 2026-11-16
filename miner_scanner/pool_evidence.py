"""Value-free pool API evidence. No networking, credentials or disk access.

Only reviewed configuration *field names and types* leave this module. Actual
pool settings and API responses must never be serialized as a support report.
Unknown field names are counted, because dynamic dictionary keys can themselves
contain a pool account, token or address.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from urllib.parse import unquote, urlsplit

from .diagnostic_bundle import build_bundle


_FIELDS = frozenset({
    'pools', 'pool', 'miner', 'msg', 'Msg', 'POOLS', 'code', 'desc', 'STATUS', 'Status', 'status',
    'network', 'ui', 'regional', 'layout', 'password',
    'url', 'user', 'pass', 'URL', 'User', 'Password', 'passwd', 'worker', 'account',
    'POOL', 'index', 'pool_index', 'id', 'pool_type', 'priority', 'Priority',
    'bitmain-work-mode', 'miner-mode', 'bitmain-freq-level', 'freq-level',
    'bitmain-fan-ctrl', 'bitmain-fan-pwm', 'bitmain-hashrate-percent',
    'bitmain-freq', 'bitmain-voltage', 'bitmain-user-ip-cat', 'algo',
    'bitmain-chip-temp-threshold', 'bitmain-drop-freq-scale', 'bitmain-power-off-delay',
    'fc-work-mode', 'cooling', 'misc', 'overclock', 'fan', 'fans', 'mode',
    'fc-fan-ctrl', 'fc-fan-pwm', 'fc-freq', 'fc-voltage', 'fc-freq-level',
    'fc_fan_ctrl', 'fc_fan_pwm', 'fc_freq', 'fc_voltage', 'fc_work_mode', 'fc_freq_level',
    'hostname', 'ip', 'gateway', 'dns', 'dhcp', 'timezone', 'language',
    'find_miner', 'target_temp', 'read_only_mode',
    'api-allow', 'api-groups', 'api-listen', 'api-network',
    'bitmain-ccdelay', 'bitmain-pwth', 'bitmain-use-vil',
    'enable_working_voltage', 'freq_mode', 'hotelfeepercent', 'poolshotelfee',
    'need_update_fw_now', 'target-temp', 'target-temp-ctrl', 'working_voltage',
})
_KINDS = frozenset({'object', 'array', 'string', 'boolean', 'integer', 'number', 'null', 'unknown'})
_MARKERS = frozenset({'missing', 'empty', 'present', 'masked', 'invalid'})
_SOURCES = frozenset({'stock.cgi', 'antminer.cgi', 'pitbit.web.conf', 'vnish.settings', 'elphapex.luci', 'elphapex.web.conf',
                      'whatsminer.rpc_pools', 'cgminer.pools', 'jasminer.status', 'goldshell.pools', 'unknown'})
_READ_KINDS = frozenset({'saved_configuration', 'runtime', 'unknown'})
_BACKENDS = frozenset({'bitmain.stock.cgi', 'vnish.settings', 'whatsminer.rpc3.pools', 'elphapex.web.pools', 'pitbit.c096.web.pools'})
_REVIEW = frozenset({'ready', 'observed', 'unchanged', 'unsupported', 'auth_required', 'offline', 'changed', 'failed', 'cancelled'})
_RESULT = frozenset({'succeeded', 'unconfirmed', 'failed', 'unsupported', 'skipped', 'cancelled'})
_IDENTITY = ('make', 'model', 'firmware', 'firmware_version', 'api_version', 'profile_id', 'profile_version')
_SECRET_FIELD = re.compile(r'^(?:pools?|network|pass(?:word|wd)?|pwd|user(?:name)?|worker|account|url|token|salt|secret|authorization|cookie|api[-_]?key)$', re.I)


def _kind(value):
    return ({dict: 'object', list: 'array', str: 'string', bool: 'boolean',
             int: 'integer', float: 'number', type(None): 'null'}).get(type(value), 'unknown')


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000 else 0


def _shape(value, *, depth=0, budget=None):
    budget = budget if budget is not None else [256]
    if budget[0] <= 0:
        return {'kind': 'unknown', 'truncated': True}
    budget[0] -= 1
    if depth > 6:
        return {'kind': 'unknown', 'truncated': True}
    kind = _kind(value)
    result = {'kind': kind}
    if kind == 'object':
        result['fields'] = {}
        for key, item in value.items():
            if key not in _FIELDS:
                continue
            if budget[0] <= 0:
                result['truncated'] = True
                break
            result['fields'][key] = _shape(item, depth=depth + 1, budget=budget)
        result['unknown_fields'] = sum(key not in _FIELDS for key in value)
    elif kind == 'array':
        result['length'] = len(value)
        result['items'] = []
        for item in value[:3]:
            if budget[0] <= 0:
                result['truncated'] = True
                break
            result['items'].append(_shape(item, depth=depth + 1, budget=budget))
        if len(value) > 3:
            result['truncated'] = True
    return result


def _safe_shape(value, *, depth=0, budget=None):
    """Re-whitelist even an already detached evidence dictionary."""
    budget = budget if budget is not None else [256]
    if budget[0] <= 0:
        return {'kind': 'unknown', 'truncated': True}
    budget[0] -= 1
    if not isinstance(value, dict) or depth > 6:
        return {'kind': 'unknown', 'truncated': True}
    kind = value.get('kind')
    result = {'kind': kind if isinstance(kind, str) and kind in _KINDS else 'unknown'}
    if value.get('truncated') is True:
        result['truncated'] = True
    if result['kind'] == 'object':
        fields = value.get('fields')
        result['fields'] = {}
        for key, item in fields.items() if isinstance(fields, dict) else ():
            if key not in _FIELDS:
                continue
            if budget[0] <= 0:
                result['truncated'] = True
                break
            result['fields'][key] = _safe_shape(item, depth=depth + 1, budget=budget)
        result['unknown_fields'] = _count(value.get('unknown_fields'))
    elif result['kind'] == 'array':
        result['length'] = _count(value.get('length'))
        items = value.get('items')
        result['items'] = []
        for item in items[:3] if isinstance(items, list) else ():
            if budget[0] <= 0:
                result['truncated'] = True
                break
            result['items'].append(_safe_shape(item, depth=depth + 1, budget=budget))
    return result


def _marker(item, names):
    for name in names:
        if name not in item:
            continue
        value = item[name]
        if not isinstance(value, str):
            return 'invalid'
        if not value:
            return 'empty'
        return 'masked' if set(value) <= {'*', '•'} else 'present'
    return 'missing'


def configuration_evidence(config, source='unknown', *, record=None, reading_kind=None, secrets=()):
    """Describe only shape/presence. Strings, numeric values and keys are data."""
    result = {'read_source': source if isinstance(source, str) and source in _SOURCES else 'unknown',
              'configuration_schema': _shape(config), 'pool_slots': []}
    if reading_kind is None:
        reading_kind = 'saved_configuration' if source in ('stock.cgi', 'pitbit.web.conf', 'vnish.settings', 'elphapex.web.conf') else 'unknown'
    result['reading_kind'] = reading_kind if isinstance(reading_kind, str) and reading_kind in _READ_KINDS else 'unknown'
    if record is not None:
        result['identity'] = identity_evidence(record, secrets=_secrets(config) + _secrets(secrets, sensitive=True))
    root = config
    if isinstance(root, dict):
        for key in ('miner', 'msg', 'Msg'):
            if isinstance(root.get(key), dict) and ('pools' in root[key] or 'POOLS' in root[key]):
                root = root[key]
                break
    slots = root.get('pools', root.get('POOLS')) if isinstance(root, dict) else root if isinstance(root, list) else None
    if isinstance(slots, dict):
        slots = slots.get('pool')
    if isinstance(slots, list):
        for position, item in enumerate(slots[:3], 1):
            markers = ({'url': _marker(item, ('url', 'URL')), 'user': _marker(item, ('user', 'User', 'worker', 'account')),
                        'pass': _marker(item, ('pass', 'passwd', 'password', 'Password'))}
                       if isinstance(item, dict) else {key: 'invalid' for key in ('url', 'user', 'pass')})
            result['pool_slots'].append({'position': position, 'fields': markers})
    return result


def api_evidence(spec):
    """Only operation presence, never schema descriptions/examples or URLs."""
    paths = spec.get('paths') if isinstance(spec, dict) else None
    settings = paths.get('/settings') if isinstance(paths, dict) else None
    return {key: isinstance(settings, dict) and isinstance(settings.get(method), dict)
            for key, method in (('settings_get', 'get'), ('settings_post', 'post'))}


def _secrets(value, *, sensitive=False, depth=0, budget=None):
    budget = budget if budget is not None else [1024]
    if depth > 8 or budget[0] <= 0:
        return []
    budget[0] -= 1
    if isinstance(value, str):
        if not sensitive or not value or len(value) > 4096:
            return []
        values = [value]
        token = re.fullmatch(r'Bearer\s+(\S+)', value, re.I)
        if token:
            values.append(token[1])
        if '@' in value:
            try:
                parts = urlsplit(value if '://' in value else '//' + value)
                values.extend(unquote(item) for item in (parts.username, parts.password) if item)
            except ValueError:
                pass
        return values
    if isinstance(value, dict):
        return [text for key, item in list(value.items())[:128]
                for text in _secrets(item, sensitive=sensitive or bool(_SECRET_FIELD.fullmatch(str(key))),
                                     depth=depth + 1, budget=budget)]
    if isinstance(value, (tuple, list)):
        return [text for item in value[:128] for text in _secrets(item, sensitive=sensitive, depth=depth + 1, budget=budget)]
    return []


def identity_evidence(record=None, row=None, *, secrets=()):
    """Reuse existing support-package redaction for technical identity strings."""
    if record is not None:
        identity = record.identity
        row = {'Identity': {key: getattr(identity, key, None) for key in (
            *_IDENTITY, 'ip', 'mac', 'serial', 'device_id', 'fingerprint')}}
    row = row if isinstance(row, dict) else {}
    if isinstance(secrets, str):
        secrets = (secrets,)
    elif not isinstance(secrets, (tuple, list, set, frozenset)):
        secrets = ()
    identity = row.get('Identity') if isinstance(row.get('Identity'), dict) else {}
    display_fields = {'make': 'Make', 'model': 'Model', 'firmware': 'Firmware',
                      'firmware_version': 'FirmwareVersion', 'api_version': 'ApiVersion', 'profile_id': 'ProfileId'}
    sensitive = [v for v in secrets if isinstance(v, str) and v]
    sensitive += [identity.get(key) for key in ('ip', 'mac', 'serial', 'device_id', 'fingerprint')
                  if isinstance(identity.get(key), str) and identity[key]]
    sensitive += [row[key] for key in ('IP', 'MAC', 'Mac', 'SN', 'Serial', 'DeviceId')
                  if isinstance(row.get(key), str) and row[key]]
    projected = {key: row.get(display_fields[key]) or identity.get(key) for key in display_fields}
    cleaned = build_bundle({'Identity': projected}, secrets=tuple(v for v in sensitive if len(v) >= 4))['snapshot']['identity']
    result = {key: cleaned.get(key) for key in display_fields}
    for key, value in projected.items():
        if isinstance(value, str) and value in sensitive:
            result[key] = None
        elif (key in {'firmware_version', 'api_version'} and isinstance(value, str)
                and re.fullmatch(r'\d{1,9}(?:\.\d{1,9}){3}', value)
                and not any(len(secret) >= 4 and secret in value for secret in sensitive)):
            # Four-part version syntax resembles IPv4. Retain technical version
            # data while still removing actual known device/pool identifiers.
            result[key] = value
    version = identity.get('profile_version')
    result['profile_version'] = version if type(version) is int and 1 <= version <= 1_000_000 else None
    return result


def _sanitize(evidence, *, secrets=()):
    evidence = evidence if isinstance(evidence, dict) else {}
    identity = evidence.get('identity')
    identity = identity if isinstance(identity, dict) else {}
    review = evidence.get('review_status')
    backend = evidence.get('backend')
    result = {'identity': identity_evidence(row={'Identity': identity}, secrets=secrets),
              'review_status': review if isinstance(review, str) and review in _REVIEW else 'failed',
              'backend': backend if isinstance(backend, str) and backend in _BACKENDS else None}
    if isinstance(evidence.get('result_status'), str) and evidence.get('result_status') in _RESULT:
        result['result_status'] = evidence['result_status']
    source = evidence.get('read_source')
    result['read_source'] = source if isinstance(source, str) and source in _SOURCES else 'unknown'
    kind = evidence.get('reading_kind')
    result['reading_kind'] = kind if isinstance(kind, str) and kind in _READ_KINDS else 'unknown'
    if isinstance(evidence.get('configuration_schema'), dict):
        shape = _safe_shape(evidence['configuration_schema'])
        result['configuration_schema'] = shape
        result['schema_digest'] = sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()
    result['pool_slots'] = []
    slots = evidence.get('pool_slots')
    for position, slot in enumerate(slots[:3] if isinstance(slots, list) else [], 1):
        fields = slot.get('fields') if isinstance(slot, dict) else None
        fields = fields if isinstance(fields, dict) else {}
        result['pool_slots'].append({'position': position, 'fields': {
            key: fields.get(key) if isinstance(fields.get(key), str) and fields.get(key) in _MARKERS else 'invalid'
            for key in ('url', 'user', 'pass')}})
    api = evidence.get('api')
    if isinstance(api, dict):
        result['api'] = {key: api.get(key) is True for key in ('settings_get', 'settings_post')}
    return result


def evidence_from_state(state, record, *, secrets=()):
    evidence = configuration_evidence(state.opaque.get('config'),
        {'bitmain.stock.cgi': 'stock.cgi', 'vnish.settings': 'vnish.settings',
         'elphapex.web.pools': 'elphapex.web.conf',
         'pitbit.c096.web.pools': 'pitbit.web.conf',
         'whatsminer.rpc3.pools': 'whatsminer.rpc_pools'}.get(state.backend_id, 'unknown'),
        reading_kind='runtime' if state.backend_id == 'whatsminer.rpc3.pools' else None)
    evidence['identity'] = identity_evidence(record, secrets=_secrets(state.opaque.get('config'))
        + _secrets(state.opaque.get('headers'), sensitive=True)
        + _secrets(state.opaque.get('ephemeral_secrets'), sensitive=True)
        + _secrets(secrets, sensitive=True))
    evidence['backend'] = state.backend_id
    if isinstance(state.opaque.get('safe_evidence'), dict):
        evidence.update({key: state.opaque['safe_evidence'][key] for key in ('api',) if key in state.opaque['safe_evidence']})
    return _sanitize(evidence)


def sanitize_evidence(evidence, *, secrets=()):
    """Detach and validate the public evidence attached to a preview."""
    return _sanitize(evidence, secrets=_secrets(secrets, sensitive=True))


def evidence_from_plan(plan):
    evidence = dict(getattr(plan, 'evidence', {}) or {})
    identity = evidence.get('identity')
    if not isinstance(identity, dict):
        identity = {'model': getattr(plan, 'model', None), 'firmware': getattr(plan, 'firmware', None),
                    'firmware_version': getattr(plan, 'firmware_version', None),
                    'api_version': getattr(plan, 'api_version', None), 'profile_id': getattr(plan, 'profile_id', None)}
    evidence['identity'] = identity_evidence(row={'Identity': {
        **identity, 'ip': getattr(plan, 'ip', None), 'device_id': getattr(plan, 'device_id', None)}})
    evidence['review_status'] = getattr(plan, 'status', 'failed')
    return _sanitize(evidence)


def evidence_report(plans):
    """A bounded detached report; never serialize plans, private state or map keys."""
    devices = []
    for index, value in enumerate(plans):
        if index >= 4096:
            raise ValueError('Too many devices in compatibility report')
        evidence = _sanitize(value) if isinstance(value, dict) else evidence_from_plan(value)
        devices.append({'device': f'device-{index + 1:03}', **evidence})
    result = {'report_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
              'kind': 'pool_api_compatibility', 'hardware_verified': False, 'devices': devices}
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 16 * 1024 * 1024:
        raise ValueError('Compatibility report is too large')
    return result
