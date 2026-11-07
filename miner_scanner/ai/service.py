"""Execute one explicitly requested provider call and validate its references."""
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
import json
import re
from threading import Event

from .contracts import AnalysisError, AnalysisReport, OUTPUT_SCHEMA, PreparedAnalysis


class _OutputViolation(ValueError):
    """Closed diagnostic codes only; never copy a response value into an error."""
    def __init__(self, reason, field='', *, stage='output'):
        self.reason, self.field, self.stage = reason, field, stage
        super().__init__(reason)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _OutputViolation('duplicate_key')
        result[key] = value
    return result


def _text(value, limit, field):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise _OutputViolation('text_value', field)


def _snapshot_reference(reference, payload, field):
    # Arrays in supplied telemetry are real evidence too. Resolve each actual
    # container; never treat a string as an indexable snapshot array.
    if not re.fullmatch(r'snapshot(?:\.[A-Za-z_][A-Za-z_0-9]*|\[(?:0|[1-9][0-9]{0,2})\]){1,5}', reference):
        raise _OutputViolation('snapshot_reference_format', field, stage='evidence')
    value = payload['snapshot']
    for key, index in re.findall(r'\.([A-Za-z_][A-Za-z_0-9]*)|\[([0-9]+)\]', reference[len('snapshot'):]):
        if key:
            if not isinstance(value, dict) or key not in value:
                raise _OutputViolation('snapshot_reference_missing', field, stage='evidence')
            value = value[key]
        else:
            if not isinstance(value, list) or int(index) >= len(value):
                raise _OutputViolation('snapshot_reference_missing', field, stage='evidence')
            value = value[int(index)]


def _response_object(text):
    if not isinstance(text, str):
        raise _OutputViolation('output_type')
    if len(text.encode('utf-8')) > 65536:
        raise _OutputViolation('output_size')
    # A single complete JSON fence is presentation only. Never search prose for
    # braces, merge blocks, repair JSON or discard trailing response content.
    stripped = text.strip()
    fenced = re.fullmatch(r'```json[ \t]*\r?\n(.*?)\r?\n```', stripped, flags=re.S)
    document = fenced[1] if fenced else stripped
    try:
        return json.loads(document, object_pairs_hook=_object,
            parse_constant=lambda value: (_ for _ in ()).throw(_OutputViolation('output_json')))
    except json.JSONDecodeError:
        raise _OutputViolation('output_json') from None


def validate_output(text, prepared):
    try:
        data = _response_object(text)
        if not isinstance(data, dict) or set(data) != set(OUTPUT_SCHEMA['required']):
            raise _OutputViolation('output_fields')
        if data['status'] not in ('healthy', 'warning', 'critical', 'unknown'):
            raise _OutputViolation('status_value', 'status')
        _text(data['summary'], 2000, 'summary')
        for key, limit in (('evidence', 12), ('likely_causes', 3), ('next_checks', 5), ('missing_data', 8)):
            if not isinstance(data[key], list) or len(data[key]) > limit:
                raise _OutputViolation('list_value', key)
        payload = json.loads(prepared.payload_json)
        included = {item['line'] for item in payload['excerpt']['lines']}
        for index, item in enumerate(data['evidence']):
            field = f'evidence[{index}]'
            if not isinstance(item, dict) or set(item) != {'source', 'reference', 'observation'}:
                raise _OutputViolation('evidence_shape', field, stage='evidence')
            _text(item['observation'], 1600, field + '.observation')
            _text(item['reference'], 180, field + '.reference')
            reference = item['reference']
            if item['source'] == 'log':
                match = re.fullmatch(r'L([1-9][0-9]{0,7})(?:-L([1-9][0-9]{0,7}))?', reference)
                if not match:
                    raise _OutputViolation('log_reference_format', field + '.reference', stage='evidence')
                start, end = int(match[1]), int(match[2] or match[1])
                if end < start or end - start > len(included) or any(number not in included for number in range(start, end + 1)):
                    raise _OutputViolation('log_reference_missing', field + '.reference', stage='evidence')
            elif item['source'] == 'snapshot':
                _snapshot_reference(reference, payload, field + '.reference')
            else:
                raise _OutputViolation('evidence_source', field + '.source', stage='evidence')
        for index, item in enumerate(data['likely_causes']):
            field = f'likely_causes[{index}]'
            if not isinstance(item, dict) or set(item) != {'cause', 'confidence'}:
                raise _OutputViolation('cause_shape', field)
            if item['confidence'] not in ('low', 'medium', 'high'):
                raise _OutputViolation('confidence_value', field + '.confidence')
            _text(item['cause'], 900, field + '.cause')
        for key in ('next_checks', 'missing_data'):
            for index, item in enumerate(data[key]):
                _text(item, 900, f'{key}[{index}]')
        if data['status'] in ('healthy', 'warning', 'critical') and not data['evidence']:
            raise _OutputViolation('evidence_required', 'evidence', stage='evidence')
        return data
    except _OutputViolation as error:
        details = dict(validation_stage=error.stage, validation_reason=error.reason)
        if error.field:
            details['validation_field'] = error.field
        raise AnalysisError('invalid_response', details=details) from None
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise AnalysisError('invalid_response', details=dict(validation_stage='output',
            validation_reason='output_structure')) from None


def analyze(prepared, config, api_key='', *, cancel=None, chatgpt_store=None):
    from .yandex import YandexProvider
    cancel = cancel or Event()
    if cancel.is_set():
        raise AnalysisError('cancelled')
    try:
        if not isinstance(prepared, PreparedAnalysis):
            raise ValueError()
        digest = sha256((prepared.instructions + '\0' + prepared.payload_json).encode('utf-8')).hexdigest()
        if not compare_digest(digest, prepared.input_sha256):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, UnicodeError):
        raise AnalysisError('invalid_input') from None
    if config.provider == 'chatgpt':
        from .openai import OpenAIProvider
        if chatgpt_store is None:
            raise AnalysisError('auth_required')
        reply = OpenAIProvider(config, chatgpt_store).analyze(prepared, cancel=cancel)
    elif config.provider == 'yandex':
        reply = YandexProvider(config, api_key).analyze(prepared, cancel=cancel)
    else:
        raise AnalysisError('configuration')
    if cancel.is_set():
        raise AnalysisError('cancelled')
    data = validate_output(reply.text, prepared)
    if cancel.is_set():
        raise AnalysisError('cancelled')
    payload = json.loads(prepared.payload_json)
    telemetry = payload['snapshot'].get('telemetry', {})
    log, excerpt = payload['log'], payload['excerpt']
    provenance = dict(observed_at=telemetry.get('observed_at'), stale=telemetry.get('stale'),
        log_collected_at=log.get('collected_at'), log_kind=log.get('kind'), truncated=log.get('truncated'),
        included_lines=excerpt['included_lines'], omitted_lines=excerpt['omitted_lines'],
        shortened_lines=excerpt['shortened_lines'])
    return AnalysisReport(json.dumps(data, ensure_ascii=False), config.provider, reply.model,
        datetime.now(timezone.utc).isoformat(), prepared.input_sha256, prepared.skill_version,
        prepared.skill_sha256, reply.input_tokens, reply.output_tokens, json.dumps(provenance, ensure_ascii=False))
