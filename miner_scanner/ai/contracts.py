"""Provider-independent, immutable input and validated diagnostic output."""
from dataclasses import dataclass
from collections.abc import Mapping
import json
import re
from types import MappingProxyType


_ERROR_IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,95}\Z')
_ERROR_PARAMETER = re.compile(
    r'(?:model|instructions|input|store|stream|text|tools|tool_choice|reasoning|'
    r'background|conversation|max_output_tokens|max_tool_calls|metadata|moderation|'
    r'multi_agent|prompt|prompt_cache_retention|safety_identifier|temperature|'
    r'top_logprobs|top_p|truncation|user|previous_response_id|service_tier|'
    r'additional_tools|include|parallel_tool_calls)'
    r'(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[0-9]{1,4}\])*\Z')
_REQUEST_ID = re.compile(
    r'(?:req_[A-Za-z0-9_-]{1,96}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z')
_ERROR_SHAPES = frozenset(('error', 'detail', 'response_error', 'event_error', 'non_json', 'unknown'))
_VALIDATION_STAGES = frozenset(('stream', 'output', 'evidence'))
_VALIDATION_REASONS = frozenset((
    'stream_format', 'stream_json', 'stream_incomplete', 'output_refusal',
    'output_channel', 'output_missing', 'output_tool', 'output_json',
    'duplicate_key', 'output_size', 'output_type', 'output_fields', 'status_value',
    'text_value', 'list_value', 'evidence_shape', 'evidence_source',
    'log_reference_format', 'log_reference_missing', 'snapshot_reference_format',
    'snapshot_reference_missing', 'cause_shape', 'confidence_value',
    'evidence_required', 'output_structure'))
_VALIDATION_FIELD = re.compile(
    r'(?:status|summary|evidence|likely_causes|next_checks|missing_data)'
    r'(?:\[[0-9]{1,3}\])?(?:\.(?:source|reference|observation|cause|confidence))?\Z')


def _error_details(details):
    """Copy only bounded technical identifiers; never accept server prose."""
    clean = {}
    if isinstance(details, Mapping):
        status = details.get('http_status')
        if type(status) is int and 100 <= status <= 599:
            clean['http_status'] = status
        for name, pattern, maximum in (
                ('provider_code', _ERROR_IDENTIFIER, 96),
                ('provider_param', _ERROR_PARAMETER, 128),
                ('request_id', _REQUEST_ID, 100)):
            value = details.get(name)
            if isinstance(value, str) and len(value) <= maximum and pattern.fullmatch(value):
                clean[name] = value
        shape = details.get('response_shape')
        if isinstance(shape, str) and shape in _ERROR_SHAPES:
            clean['response_shape'] = shape
        for name, allowed in (('validation_stage', _VALIDATION_STAGES),
                              ('validation_reason', _VALIDATION_REASONS)):
            value = details.get(name)
            if isinstance(value, str) and value in allowed:
                clean[name] = value
        field = details.get('validation_field')
        if isinstance(field, str) and len(field) <= 96 and _VALIDATION_FIELD.fullmatch(field):
            clean['validation_field'] = field
    return MappingProxyType(clean)


class AnalysisError(Exception):
    """Public error code and optional technical identifiers, never response prose."""
    def __init__(self, code, *, details=None):
        self.code = code if isinstance(code, str) and code.replace('_', '').isalnum() and len(code) < 48 else 'error'
        self.details = _error_details(details)
        super().__init__(self.code)


@dataclass(frozen=True)
class ProviderReply:
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class PreparedAnalysis:
    instructions: str
    payload_json: str
    input_sha256: str
    skill_version: str
    included_lines: int
    omitted_lines: int
    skill_sha256: str = ''

    @property
    def preview(self):
        return '--- Diagnostic input ---\n' + self.payload_json + '\n\n--- Instructions ---\n' + self.instructions


@dataclass(frozen=True)
class AnalysisReport:
    result_json: str
    provider: str
    model: str
    created_at: str
    input_sha256: str
    skill_version: str
    skill_sha256: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    provenance_json: str = '{}'

    def to_dict(self):
        return dict(schema_version=1, analysis=json.loads(self.result_json), provider=self.provider,
                    model=self.model, created_at=self.created_at, input_sha256=self.input_sha256,
                    skill_version=self.skill_version, skill_sha256=self.skill_sha256,
                    input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                    read_only=True, provenance=json.loads(self.provenance_json))

    def display_text(self, language='ru'):
        data = json.loads(self.result_json)
        en = language == 'en'
        status = ({'healthy': 'Healthy', 'warning': 'Warning', 'critical': 'Critical', 'unknown': 'Unknown'} if en else
                  {'healthy': 'В норме', 'warning': 'Требует внимания', 'critical': 'Критично', 'unknown': 'Недостаточно данных'})
        confidence = ({'low': 'Low', 'medium': 'Medium', 'high': 'High'} if en else
                      {'low': 'Низкая', 'medium': 'Средняя', 'high': 'Высокая'})
        lines = [('AI assessment: ' if en else 'Оценка ИИ: ') + status[data['status']], '', data['summary']]
        provenance = json.loads(self.provenance_json)
        if provenance:
            lines += ['', ('Snapshot time: ' if en else 'Время снимка: ') + str(provenance.get('observed_at') or '—'),
                      ('Log collected: ' if en else 'Лог получен: ') + str(provenance.get('log_collected_at') or '—'),
                      ('Log kind: ' if en else 'Источник лога: ') + str(provenance.get('log_kind') or '—'),
                      ('Included / omitted lines: ' if en else 'Передано / пропущено строк: ') +
                      str(provenance.get('included_lines', 0)) + ' / ' + str(provenance.get('omitted_lines', 0))]
            if provenance.get('stale'):
                lines.append('Snapshot is stale.' if en else 'Показания устройства устарели.')
            if provenance.get('truncated') or provenance.get('shortened_lines'):
                lines.append('The log or selected lines were shortened.' if en else 'Лог или выбранные строки были сокращены.')
        sections = [
            ('Evidence' if en else 'Наблюдения', [f"[{item['reference']}] {item['observation']}" for item in data['evidence']]),
            ('Likely causes' if en else 'Вероятные причины', [item['cause'] + ' (' + confidence[item['confidence']] + ')' for item in data['likely_causes']]),
            ('Next checks' if en else 'Следующие проверки', data['next_checks']),
            ('Missing data' if en else 'Недостающие данные', data['missing_data'])]
        for title, items in sections:
            lines += ['', title, *(['• ' + item for item in items] or ['—'])]
        lines += ['', f'{self.provider} · {self.model} · {self.created_at}',
                  'Input SHA256: ' + self.input_sha256,
                  ('AI interpretation; verify the cited evidence.' if en else 'Заключение ИИ; проверьте приведённые наблюдения.')]
        return '\n'.join(lines)


OUTPUT_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['status', 'summary', 'evidence', 'likely_causes', 'next_checks', 'missing_data'],
    'properties': {
        'status': {'type': 'string', 'enum': ['healthy', 'warning', 'critical', 'unknown']},
        'summary': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
        'evidence': {'type': 'array', 'maxItems': 12, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['source', 'reference', 'observation'],
            'properties': {'source': {'type': 'string', 'enum': ['log', 'snapshot']}, 'reference': {'type': 'string', 'maxLength': 180},
                           'observation': {'type': 'string', 'maxLength': 1600}}}},
        'likely_causes': {'type': 'array', 'maxItems': 3, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['cause', 'confidence'],
            'properties': {'cause': {'type': 'string', 'maxLength': 900},
                           'confidence': {'type': 'string', 'enum': ['low', 'medium', 'high']}}}},
        'next_checks': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string', 'maxLength': 900}},
        'missing_data': {'type': 'array', 'maxItems': 8, 'items': {'type': 'string', 'maxLength': 900}}}}
