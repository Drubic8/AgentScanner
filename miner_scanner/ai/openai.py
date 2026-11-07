"""ChatGPT-account model discovery and bounded, completed Responses inference.

This module uses the OAuth account granted by Sign in with ChatGPT. It never
accepts an API key or changes the billing path after a plan-usage failure.
Protocol: https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference
"""
import json
import re
import time
from threading import Event

from .chatgpt_auth import ensure_access
from .contracts import AnalysisError, OUTPUT_SCHEMA, ProviderReply
from .https_transport import request_bytes


HOST = 'api.openai.com'
MODELS_PATH = '/v1/models'
RESPONSES_PATH = '/v1/responses'
MAX_CATALOG_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 128 * 1024
MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024
MAX_TEXT_CHARS = 64 * 1024
_CONNECTION_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['connection'], 'properties': {'connection': {'type': 'string', 'enum': ['ok']}}}
_ACTIVE_EVENT_FIELDS = frozenset(('response', 'output', 'item', 'part', 'delta', 'text',
                                  'refusal', 'tool_calls', 'function_call', 'error', 'code'))
_MODEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_ERRORS = {
    'subscription_sharing_user_not_eligible': 'chatgpt_not_eligible',
    'subscription_sharing_usage_limit_exceeded': 'chatgpt_usage_limit',
    'subscription_sharing_usage_unavailable': 'chatgpt_usage_unavailable',
    'subscription_sharing_unsupported_capability': 'chatgpt_capability',
    'subscription_sharing_route_not_supported': 'chatgpt_route',
    'subscription_sharing_invalid_user': 'auth_required',
    'chatpass_v2_scope_not_authorized': 'chatgpt_scope',
    'chatpass_v2_invalid_authorization_context': 'chatgpt_scope',
    'invalid_api_key': 'auth_required',
    'model_not_found': 'model_unavailable',
    'rate_limit_exceeded': 'chatgpt_usage_limit',
    'insufficient_quota': 'chatgpt_usage_limit',
    'credit_balance_exhausted': 'chatgpt_usage_limit',
    'unsupported_parameter': 'invalid_request',
    'unsupported_value': 'invalid_request',
    'invalid_value': 'invalid_request',
    'invalid_type': 'invalid_request',
    'missing_required_parameter': 'invalid_request',
    'invalid_request_error': 'invalid_request',
    'context_length_exceeded': 'too_large',
    'authentication_error': 'auth_required',
    'permission_denied': 'forbidden',
    'server_error': 'server',
}
_PARAMS = frozenset(('model', 'instructions', 'input', 'input[0]', 'input[0].role',
    'input[0].type', 'input[0].content', 'input[0].content[0]',
    'input[0].content[0].type', 'input[0].content[0].text', 'store', 'stream',
    'text', 'text.format', 'text.format.type', 'text.format.name', 'text.format.schema', 'text.format.strict',
    'background', 'conversation', 'max_output_tokens', 'max_tokens', 'max_tool_calls',
    'metadata', 'moderation', 'multi_agent', 'prompt', 'prompt_cache_retention',
    'safety_identifier', 'temperature', 'top_logprobs', 'top_p', 'truncation', 'user',
    'previous_response_id', 'tools', 'tool_choice', 'service_tier', 'include',
    'reasoning', 'reasoning.effort'))
_REQUEST_ID = re.compile(r'(?:req_[A-Za-z0-9_-]{1,124}|[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})\Z')


def _fail(code, *, details=None):
    raise AnalysisError(code, details=details) from None


def _invalid(stage, reason):
    _fail('invalid_response', details={'validation_stage': stage, 'validation_reason': reason})


def _remaining(deadline, cancel):
    if cancel.is_set():
        _fail('cancelled')
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        _fail('timeout')
    return seconds


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _json(value):
    try:
        return json.loads(value, object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail('invalid_response')


def _no_secret(value, secrets):
    """Check decoded JSON, including credentials hidden by JSON escaping."""
    try:
        decoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail('invalid_response')
    if any(secret in decoded for secret in secrets):
        _fail('invalid_response')


def _header(reply, name):
    for key, value in reply.headers.items():
        if isinstance(key, str) and key.lower() == name.lower():
            return value if isinstance(value, str) else ''
    return ''


def _error_object(value):
    if isinstance(value, dict):
        if isinstance(value.get('error'), dict):
            return value['error'], 'error'
        if isinstance(value.get('response'), dict) and isinstance(value['response'].get('error'), dict):
            return value['response']['error'], 'response_error'
        if value.get('type') == 'error' or isinstance(value.get('code'), str):
            return value, 'event_error'
        if 'detail' in value:
            return {}, 'detail'
    return {}, 'unknown'


def _error_code(status, value=None):
    error, _ = _error_object(value)
    if isinstance(error.get('code'), str):
        mapped = _ERRORS.get(error['code'])
        if mapped:
            return mapped
    return {400: 'invalid_request', 401: 'auth_required', 403: 'forbidden',
            404: 'model_unavailable', 408: 'timeout', 429: 'chatgpt_usage_limit',
            503: 'chatgpt_usage_unavailable'}.get(status,
                'server' if 500 <= status <= 599 else 'invalid_response')


def _error_details(status, value, request_id='', *, non_json=False):
    """Retain support identifiers, never free-form server messages or values."""
    error, shape = _error_object(value)
    details = {'response_shape': 'non_json' if non_json else shape}
    if type(status) is int and 100 <= status <= 599:
        details['http_status'] = status
    if isinstance(error.get('code'), str) and error['code'] in _ERRORS:
        details['provider_code'] = error['code']
    if isinstance(error.get('param'), str) and error['param'] in _PARAMS:
        details['provider_param'] = error['param']
    if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
        details['request_id'] = request_id
    return details


def _http_error(reply, secrets):
    request_id = _header(reply, 'X-Request-Id')
    _no_secret(request_id, secrets)
    non_json = False
    try:
        value = _json(reply.body)
    except AnalysisError:
        value = None
        non_json = True
    if value is not None:
        _no_secret(value, secrets)
    _fail(_error_code(reply.status, value), details=_error_details(
        reply.status, value, request_id, non_json=non_json))


def _token_count(value):
    return value if type(value) is int and 0 <= value < 100_000_000 else None


def _events(body):
    if not isinstance(body, bytes) or not body or len(body) > MAX_STREAM_BYTES:
        _invalid('stream', 'stream_format')
    try:
        text = body.decode('utf-8-sig')
    except UnicodeError:
        _invalid('stream', 'stream_format')
    data, event, count = [], '', 0
    # SSE uses CR, LF or CRLF, rather than all Unicode line separators.
    for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        if not line:
            if data:
                count += 1
                if count > 4096:
                    _invalid('stream', 'stream_format')
                value = '\n'.join(data)
                if len(value.encode('utf-8')) > MAX_EVENT_BYTES:
                    _invalid('stream', 'stream_format')
                yield event, value
            data, event = [], ''
        elif not line.startswith(':'):
            field, _, value = line.partition(':')
            if value.startswith(' '):
                value = value[1:]
            if field == 'data':
                data.append(value)
            elif field == 'event':
                event = value
    # An unterminated data event is an interrupted stream, not a completion.
    if data:
        _invalid('stream', 'stream_incomplete')


def _completed_reply(response, model):
    if (not isinstance(response, dict) or response.get('status') != 'completed'
            or response.get('error') or response.get('incomplete_details')):
        _invalid('stream', 'stream_incomplete')
    output = response.get('output')
    if not isinstance(output, list) or len(output) > 128:
        _invalid('output', 'output_missing')
    parts, final_parts = [], []
    has_commentary, has_final = False, False
    for item in output:
        if not isinstance(item, dict):
            _invalid('output', 'output_missing')
        if item.get('type') == 'reasoning':
            continue
        if item.get('type') != 'message' or item.get('tool_calls') or item.get('function_call'):
            _invalid('output', 'output_tool')
        if item.get('role') != 'assistant' or item.get('status') not in ('completed', None):
            _invalid('output', 'output_channel')
        phase = item.get('phase')
        if phase not in (None, 'commentary', 'final_answer'):
            _invalid('output', 'output_channel')
        has_commentary = has_commentary or phase == 'commentary'
        has_final = has_final or phase == 'final_answer'
        content = item.get('content')
        if not isinstance(content, list) or len(content) > 128:
            _invalid('output', 'output_missing')
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'refusal':
                _invalid('output', 'output_refusal')
            if (not isinstance(part, dict) or part.get('type') != 'output_text'
                    or not isinstance(part.get('text'), str)):
                _invalid('output', 'output_missing')
            if phase == 'final_answer':
                final_parts.append(part['text'])
            elif phase is None:
                parts.append(part['text'])
    # A documented commentary phase is intermediate, even in a completed
    # response. Never concatenate it with the diagnostic final answer.
    if has_commentary and not has_final:
        _invalid('output', 'output_channel')
    text = ''.join(final_parts if has_final else parts)
    if not text.strip() or len(text) > MAX_TEXT_CHARS:
        _invalid('output', 'output_missing')
    usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
    return ProviderReply(text=text, model=model,
        input_tokens=_token_count(usage.get('input_tokens')),
        output_tokens=_token_count(usage.get('output_tokens')))


def _consume_stream(body, model, secrets, http_status, request_id):
    completed = None
    saw_done = False
    for event_name, text in _events(body):
        if text == '[DONE]':
            if completed is None or saw_done:
                _invalid('stream', 'stream_incomplete' if completed is None else 'stream_format')
            saw_done = True
            continue
        try:
            event = _json(text)
        except AnalysisError:
            _invalid('stream', 'stream_json')
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            _invalid('stream', 'stream_format')
        _no_secret(event, secrets)
        kind = event['type']
        if event_name and event_name != kind:
            _invalid('stream', 'stream_format')
        # Treat the observed account-route response.metadata type as ancillary,
        # independently of position; never use it as output or as completion.
        # Keep parsing so a subsequent failure or unexpected output still fails.
        if kind == 'response.metadata':
            if _ACTIVE_EVENT_FIELDS.intersection(event):
                _invalid('stream', 'stream_format')
            continue
        if saw_done:
            _invalid('stream', 'stream_format')
        if kind in ('error', 'response.failed'):
            _fail(_error_code(0, event), details=_error_details(http_status, event, request_id))
        if kind == 'response.incomplete':
            _invalid('stream', 'stream_incomplete')
        if kind.startswith('response.refusal'):
            _invalid('output', 'output_refusal')
        if 'function_call' in kind or 'tool_call' in kind:
            _invalid('output', 'output_tool')
        item = event.get('item')
        if isinstance(item, dict) and item.get('type') not in ('message', 'reasoning'):
            _invalid('output', 'output_tool')
        if kind == 'response.completed':
            if completed is not None:
                _invalid('stream', 'stream_format')
            completed = _completed_reply(event.get('response'), model)
        elif completed is not None:
            _invalid('stream', 'stream_format')
    if completed is None:
        _invalid('stream', 'stream_incomplete')
    return completed


def _stream_reply(body, model, secrets, *, http_status=200, request_id=''):
    _no_secret(request_id, secrets)
    try:
        return _consume_stream(body, model, secrets, http_status, request_id)
    except AnalysisError as exc:
        if exc.code != 'invalid_response':
            raise
        details = _error_details(http_status, None, request_id)
        details.update(exc.details)
        if 'validation_stage' not in details:
            details.update(validation_stage='stream', validation_reason='stream_format')
        _fail(exc.code, details=details)


class OpenAIProvider:
    """Use only the selected ChatGPT account and its advertised model catalog."""
    def __init__(self, config, store):
        if getattr(config, 'provider', None) != 'chatgpt':
            _fail('configuration')
        self.config, self.store = config, store

    def __repr__(self):
        return 'OpenAIProvider(<ChatGPT account>)'

    def _account(self, cancel):
        if cancel.is_set():
            _fail('cancelled')
        record = ensure_access(self.store, cancel=cancel)
        if not isinstance(record, dict):
            _fail('auth_required')
        token = record.get('access_token')
        if (not isinstance(token, str) or not token or len(token) > 32768
                or not token.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in token)):
            _fail('auth_required')
        secrets = tuple(value for name in ('access_token', 'refresh_token', 'id_token')
                        if isinstance((value := record.get(name)), str) and value)
        return token, secrets

    def _catalog(self, token, secrets, cancel, deadline):
        reply = request_bytes(HOST, 'GET', MODELS_PATH, None,
            {'Authorization': 'Bearer ' + token, 'Accept': 'application/json',
             'User-Agent': 'ASIC-Monitor-AI'}, cancel=cancel,
            seconds=_remaining(deadline, cancel), max_bytes=MAX_CATALOG_BYTES)
        if reply.status != 200:
            _http_error(reply, secrets)
        if _header(reply, 'Content-Type').split(';', 1)[0].strip().lower() != 'application/json':
            _fail('invalid_response')
        if not isinstance(reply.body, bytes) or len(reply.body) > MAX_CATALOG_BYTES:
            _fail('invalid_response')
        data = _json(reply.body)
        _no_secret(data, secrets)
        if not isinstance(data, dict) or not isinstance(data.get('models'), list) or len(data['models']) > 1024:
            _fail('invalid_response')
        models = []
        for item in data['models']:
            if not isinstance(item, dict):
                _fail('invalid_response')
            if item.get('visibility') != 'list':
                continue
            slug = item.get('slug')
            if not isinstance(slug, str) or not _MODEL.fullmatch(slug):
                _fail('invalid_response')
            if slug not in models:
                models.append(slug)
        _remaining(deadline, cancel)
        return models

    def list_models(self, *, cancel=None):
        cancel = cancel if cancel is not None else Event()
        token, secrets = self._account(cancel)
        return self._catalog(token, secrets, cancel, time.monotonic() + self.config.timeout_seconds)

    def _respond(self, instructions, payload, *, cancel, schema, schema_name):
        if (not isinstance(instructions, str) or not instructions.strip()
                or not isinstance(payload, str)):
            _fail('invalid_input')
        try:
            if len(instructions.encode('utf-8')) > 16 * 1024 or len(payload.encode('utf-8')) > 96 * 1024:
                _fail('too_large')
            data = json.loads(payload)
            if not isinstance(data, dict):
                _fail('invalid_input')
            model = self.config.model
            if not isinstance(model, str) or not _MODEL.fullmatch(model):
                _fail('model_unavailable')
            # Structured Outputs constrain the final answer's shape. Source
            # references and diagnostic semantics still require local checks.
            body = json.dumps({'model': model, 'instructions': instructions,
                'input': [{'role': 'user', 'content': payload}], 'store': False, 'stream': True,
                'text': {'format': {'type': 'json_schema', 'name': schema_name,
                                    'strict': True, 'schema': schema}}},
                ensure_ascii=False, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, UnicodeError, RecursionError):
            _fail('invalid_input')
        if len(body) > MAX_REQUEST_BYTES:
            _fail('too_large')
        token, secrets = self._account(cancel)
        deadline = time.monotonic() + self.config.timeout_seconds
        if model not in self._catalog(token, secrets, cancel, deadline):
            _fail('model_unavailable')
        reply = request_bytes(HOST, 'POST', RESPONSES_PATH, body,
            {'Authorization': 'Bearer ' + token, 'Accept': 'text/event-stream',
             'Content-Type': 'application/json', 'User-Agent': 'ASIC-Monitor-AI'},
            cancel=cancel, seconds=_remaining(deadline, cancel), max_bytes=MAX_STREAM_BYTES)
        if reply.status != 200:
            _http_error(reply, secrets)
        # Account-route media headers are advisory. Accept only a bounded,
        # validated SSE body with a completed response, regardless of MIME label.
        _remaining(deadline, cancel)
        result = _stream_reply(reply.body, model, secrets, http_status=reply.status,
                               request_id=_header(reply, 'X-Request-Id'))
        _remaining(deadline, cancel)
        return result

    def analyze(self, prepared, *, cancel=None):
        cancel = cancel if cancel is not None else Event()
        return self._respond(getattr(prepared, 'instructions', None),
                             getattr(prepared, 'payload_json', None), cancel=cancel,
                             schema=OUTPUT_SCHEMA, schema_name='asic_diagnostic_report')

    def check_connection(self, *, cancel=None):
        cancel = cancel if cancel is not None else Event()
        result = self._respond('Return one JSON object only: {"connection":"ok"}.',
            '{"test":"ASIC Monitor connection check; no device data"}', cancel=cancel,
            schema=_CONNECTION_SCHEMA, schema_name='asic_connection_check')
        if _json(result.text) != {'connection': 'ok'}:
            _fail('invalid_response')
        return result
