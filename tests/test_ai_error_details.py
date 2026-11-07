"""The GUI error boundary accepts identifiers, never arbitrary provider text."""
import unittest

from miner_scanner.ai.contracts import AnalysisError


class AnalysisErrorDetailsTests(unittest.TestCase):
    def test_existing_code_only_contract_and_immutable_copy(self):
        self.assertEqual(str(AnalysisError('network')), 'network')
        self.assertEqual(dict(AnalysisError('network').details), {})
        source = {'http_status': 400, 'provider_code': 'unsupported_value',
                  'provider_param': 'text.format.type', 'request_id': 'req_test123',
                  'response_shape': 'error'}
        error = AnalysisError('invalid_request', details=source)
        source['http_status'] = 200
        self.assertEqual(error.details['http_status'], 400)
        self.assertEqual(error.args, ('invalid_request',))
        with self.assertRaises(TypeError):
            error.details['http_status'] = 200

    def test_prose_credentials_and_malformed_identifiers_do_not_cross_boundary(self):
        error = AnalysisError('invalid_request', details={
            'http_status': True, 'provider_code': 'bad\nsecret',
            'provider_param': 'user@example.invalid', 'request_id': 'Bearer TOKEN',
            'response_shape': 'raw response', 'message': 'PRIVATE-MINER-LOG',
            'detail': 'PASSWORD', 'access_token': 'PRIVATE-TOKEN'})
        self.assertEqual(dict(error.details), {})
        self.assertNotIn('PRIVATE', repr(error))

    def test_input_index_and_uuid_request_id_are_supported(self):
        error = AnalysisError('chatgpt_capability', details={
            'http_status': 200, 'provider_param': 'input[0].content[1].type',
            'request_id': '12345678-1234-5678-1234-567812345678',
            'response_shape': 'response_error'})
        self.assertEqual(error.details['provider_param'], 'input[0].content[1].type')
        self.assertEqual(error.details['http_status'], 200)

    def test_unbounded_or_arbitrary_field_paths_are_dropped(self):
        for value in ('arbitrary_private_field', 'input\npassword', 'input.' + 'a' * 130):
            with self.subTest(value=value):
                self.assertEqual(dict(AnalysisError('error', details={'provider_param': value}).details), {})

    def test_only_closed_validation_reasons_and_schema_field_paths_are_public(self):
        details = {'validation_stage': 'evidence', 'validation_reason': 'snapshot_reference_missing',
                   'validation_field': 'evidence[1].reference'}
        self.assertEqual(dict(AnalysisError('invalid_response', details=details).details), details)
        private = AnalysisError('invalid_response', details={
            'validation_stage': 'PRIVATE-LOG', 'validation_reason': 'unrecognized_secret_value',
            'validation_field': 'snapshot.private_password', 'model_response': 'PRIVATE'})
        self.assertEqual(dict(private.details), {})

    def test_strict_output_schema_has_explicit_enum_types_and_closed_objects(self):
        from miner_scanner.ai.contracts import OUTPUT_SCHEMA

        def visit(value):
            if isinstance(value, dict):
                if 'enum' in value:
                    self.assertEqual(value.get('type'), 'string')
                if value.get('type') == 'object':
                    self.assertIs(value.get('additionalProperties'), False)
                    self.assertEqual(set(value['properties']), set(value['required']))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(OUTPUT_SCHEMA)


if __name__ == '__main__':
    unittest.main()
