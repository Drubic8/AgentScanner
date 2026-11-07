"""Prepare a bounded excerpt from the previewable, already-redacted bundle."""
from collections import deque
from hashlib import sha256
from importlib.resources import files
from io import StringIO
import json
import re

from .contracts import AnalysisError, OUTPUT_SCHEMA, PreparedAnalysis

SKILL_VERSION = '1.0.1'
MAX_LOG_CHARS = 16000
MAX_PAYLOAD_BYTES = 96 * 1024
MAX_INSTRUCTION_BYTES = 16 * 1024
_EVENT = re.compile(r'error|fail|fatal|fault|panic|warn|overheat|lost|timeout|abnormal|no\s+(?:hash|chain)|ошиб|перегрев', re.I)
_SNAPSHOT_FIELDS = {
    'identity': ('make', 'model', 'firmware', 'firmware_version', 'api_version', 'profile_id', 'identified_at'),
    'telemetry': ('rate', 'average_rate', 'uptime_seconds', 'temperatures_c', 'fan_rpm', 'algorithm',
                  'rate_unit', 'mining_state', 'observed_at', 'stale', 'identify_enabled'),
    'display': ('Status', 'Error', 'ErrorDetail', 'ErrorDetails', 'Algo', 'Real', 'Avg', 'Uptime', 'Temp', 'Fan'),
    'capabilities': ('reboot', 'led_on', 'led_off', 'sleep', 'wakeup', 'normal', 'low', 'high', 'hem')}


def _safe_value(value):
    if value is None or type(value) is bool:
        return value
    if type(value) is int and abs(value) < 10 ** 100:
        return value
    if isinstance(value, float):
        if not (-1e100 < value < 1e100):
            raise AnalysisError('invalid_input')
        return value
    if isinstance(value, str) and len(value) <= 4096:
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 256:
        return [_safe_value(item) for item in value if not isinstance(item, (dict, list, tuple))]
    raise AnalysisError('invalid_input')


def _excerpt(text, budget):
    head, tail, events = [], deque(maxlen=48), deque(maxlen=256)
    prior = deque(maxlen=2)
    pending, total = 0, 0
    for total, raw in enumerate(StringIO(text), 1):
        raw = raw.rstrip('\r\n')
        item = (total, raw[:1500], len(raw) > 1500)
        if total <= 12:
            head.append(item)
        tail.append(item)
        if _EVENT.search(raw):
            events.extend(prior)
            events.append(item)
            pending = 2
        elif pending:
            events.append(item)
            pending -= 1
        prior.append(item)
    chosen = {}
    remaining = budget
    # Keep startup provenance, then recent error contexts and the final readings.
    for number, text, shortened in [*head[:8], *reversed(events), *reversed(tail), *head[8:]]:
        if number in chosen:
            continue
        cost = len(text) + 36
        if cost <= remaining:
            chosen[number] = dict(line=number, text=text, shortened=shortened)
            remaining -= cost
    return [chosen[number] for number in sorted(chosen)], total


def _reference_examples(snapshot, lines):
    """A few actual paths, not an unbounded catalog of every array element."""
    candidates = []
    for section in ('telemetry', 'identity', 'display', 'capabilities'):
        for key, value in snapshot.get(section, {}).items():
            path = f'snapshot.{section}.{key}'
            candidates.append(path)
            if isinstance(value, list) and value:
                candidates.append(path + '[0]')
    preferred = ('snapshot.identity.model', 'snapshot.telemetry.rate',
        'snapshot.telemetry.fan_rpm[0]', 'snapshot.telemetry.temperatures_c[0]')
    paths = list(dict.fromkeys([path for path in preferred if path in candidates] + candidates))[:8]
    return json.dumps(dict(log=[f"L{item['line']}" for item in lines[:2]], snapshot=paths),
                      ensure_ascii=False, separators=(',', ':'))


def prepare_analysis(bundle, *, language='ru', max_log_chars=MAX_LOG_CHARS):
    if not isinstance(bundle, dict) or set(bundle) != {'manifest', 'snapshot', 'log'}:
        raise AnalysisError('invalid_input')
    manifest, snapshot, log = (bundle[key] for key in ('manifest', 'snapshot', 'log'))
    if not isinstance(manifest, dict) or manifest.get('read_only') is not True or manifest.get('review_required') is not True:
        raise AnalysisError('invalid_input')
    if not isinstance(log, dict) or log.get('status') != 'succeeded' or not isinstance(log.get('text'), str) or not log['text'].strip():
        raise AnalysisError('no_log')
    try:
        size = len(log['text'].encode('utf-8'))
    except UnicodeError:
        raise AnalysisError('invalid_input') from None
    if not isinstance(snapshot, dict) or size > 2 * 1024 * 1024:
        raise AnalysisError('invalid_input')
    if type(max_log_chars) is not int or not 2000 <= max_log_chars <= MAX_LOG_CHARS:
        raise AnalysisError('invalid_input')
    clean_snapshot = {}
    for section, keys in _SNAPSHOT_FIELDS.items():
        values = snapshot.get(section, {})
        if not isinstance(values, dict):
            raise AnalysisError('invalid_input')
        clean_snapshot[section] = {key: _safe_value(values[key]) for key in keys if key in values}
    lines, total = _excerpt(log['text'], max_log_chars)
    payload = dict(schema_version=1, language='en' if language == 'en' else 'ru', snapshot=clean_snapshot,
        log={key: _safe_value(log.get(key)) for key in ('kind', 'source', 'collected_at', 'truncated')},
        excerpt=dict(numbering='Line numbers refer to the redacted loaded text, not the complete device archive.',
            total_lines=total, included_lines=len(lines), omitted_lines=total - len(lines),
            shortened_lines=sum(item['shortened'] for item in lines), lines=lines))
    try:
        skill = files('miner_scanner.ai').joinpath('skills/asic-monitor/SKILL.md').read_text(encoding='utf-8')
        if len(skill.encode('utf-8')) > MAX_INSTRUCTION_BYTES:
            raise AnalysisError('skill_unavailable')
    except (OSError, UnicodeError, ModuleNotFoundError):
        raise AnalysisError('skill_unavailable') from None
    instructions = (skill + '\n\nReturn one JSON object matching this schema. Do not wrap it in Markdown. '
        'Write human-readable values in ' + ('English' if language == 'en' else 'Russian') + '.\n'
        'Keep schema keys, source identifiers, status and confidence enums, and reference paths exactly as specified; '
        'do not translate them. Evidence with source="log" must reference an actually supplied line as L123, '
        'or a range such as L123-L125 only when every line in the range was supplied. Evidence with '
        'source="snapshot" must reference an existing path beginning snapshot.; array elements use a zero-based '
        'index such as snapshot.telemetry.fan_rpm[0] only when that element exists. Never cite log.*, excerpt.* '
        'or other request metadata as snapshot evidence. These are a few valid references for this request, '
        'not an exhaustive list or evidence of a diagnosis: ' + _reference_examples(clean_snapshot, lines) + '.\n' +
        json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(',', ':')))
    try:
        if len(instructions.encode('utf-8')) > MAX_INSTRUCTION_BYTES:
            raise AnalysisError('skill_unavailable')
        payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2)
        size = len(payload_json.encode('utf-8'))
    except (ValueError, TypeError, UnicodeError):
        raise AnalysisError('invalid_input') from None
    if size > MAX_PAYLOAD_BYTES:
        raise AnalysisError('invalid_input')
    digest = sha256((instructions + '\0' + payload_json).encode('utf-8')).hexdigest()
    return PreparedAnalysis(instructions, payload_json, digest, SKILL_VERSION,
        len(lines), total - len(lines), sha256(skill.encode('utf-8')).hexdigest())
