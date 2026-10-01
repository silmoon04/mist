"""Offline contracts for the reader's bounded delivery decisions."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from duplex.affect_director import (
    AffectDirector, DECISION_SCHEMA, DELIVERIES, DIRECTOR_PROMPT,
    PROMPT_VERSION, unchanged, validate_decision,
)


FACE = {'expression': 'curious', 'variant': 1}


class FakeClient:
    def __init__(self, candidate, **options):
        self.candidate = candidate
        self.options = options
        self.requests = []
        self.closed = False

    def new_session(self):
        pass

    def ask(self, request, timeout):
        self.requests.append((json.loads(request), timeout))
        return SimpleNamespace(text=json.dumps(self.candidate), errors=[], tool_calls=[])

    def close(self):
        self.closed = True


class AffectDeliveryTests(unittest.TestCase):
    def decide(self, candidate, **snapshot):
        clients = []

        def factory(**options):
            client = FakeClient(candidate, **options)
            clients.append(client)
            return client

        director = AffectDirector(client_factory=factory)
        decision, trace = director.decide_with_trace({'current_face': FACE, **snapshot})
        self.assertTrue(clients[0].closed)
        return decision, trace, clients[0]

    def candidate(self, delivery, quote='Please explain the next step.', **fields):
        return {'change': False, **FACE, 'delivery': delivery,
                'evidence': [{'record_id': 'current_user', 'quote': quote}], **fields}

    def test_all_delivery_styles_are_bounded_and_accepted_with_public_evidence(self):
        expected = ('neutral', 'warm', 'gentle', 'bright', 'serious',
                    'curious', 'amused', 'reassuring', 'urgent')
        self.assertEqual(DELIVERIES, expected)
        self.assertEqual(DECISION_SCHEMA['properties']['delivery']['enum'], list(expected))
        for delivery in expected:
            with self.subTest(delivery=delivery):
                candidate = self.candidate(delivery)
                decision, trace, client = self.decide(
                    candidate, current_user='Please explain the next step.')
                self.assertEqual(decision, candidate)
                self.assertEqual(trace['status'], 'accepted')
                self.assertEqual(trace['prompt_version'], 'affect-director-v2.1')
                self.assertEqual(PROMPT_VERSION, trace['prompt_version'])
                self.assertEqual(client.options['system_prompt'], DIRECTOR_PROMPT)
                self.assertEqual(client.requests[0][0]['schema'], DECISION_SCHEMA)

    def test_provider_receives_strict_schema_with_separate_face_and_delivery_enums(self):
        decision, trace, client = self.decide(unchanged(FACE))
        expected = {'type': 'json_schema', 'json_schema': {
            'name': 'mist_affect_decision', 'strict': True, 'schema': DECISION_SCHEMA}}
        self.assertEqual(client.options['response_format'], expected)
        self.assertEqual(trace['output_format'], 'strict_schema')
        self.assertEqual(decision, unchanged(FACE))
        schema = client.options['response_format']['json_schema']['schema']
        face_names = schema['properties']['expression']['enum']
        delivery_names = schema['properties']['delivery']['enum']
        for delivery in ('bright', 'amused', 'gentle'):
            self.assertIn(delivery, delivery_names)
            self.assertNotIn(delivery, face_names)

    def test_every_non_neutral_delivery_still_requires_evidence(self):
        for delivery in DELIVERIES:
            if delivery == 'neutral':
                continue
            with self.subTest(delivery=delivery):
                candidate = self.candidate(delivery, evidence=[])
                with self.assertRaisesRegex(ValueError, 'missing_evidence'):
                    validate_decision(candidate, current_face=FACE, sources={})

    def test_delivery_does_not_require_matching_face_sentiment(self):
        face = {'expression': 'sad', 'variant': 1}
        candidate = self.candidate('reassuring', quote='I need some help.', **face)
        decision, trace, _ = self.decide(candidate, current_face=face,
                                       current_user='I need some help.', face_override=True)
        self.assertEqual(trace['status'], 'accepted')
        self.assertEqual(decision, candidate)
        self.assertFalse(decision['change'])

    def test_recent_face_override_survives_new_context_and_delivery(self):
        face = {'expression': 'angry', 'variant': 2}
        candidate = self.candidate('amused', quote='That was a good joke.', **face)
        decision, trace, client = self.decide(candidate, current_face=face, face_override=True,
            records=[{'id': 'previous_user', 'role': 'user', 'text': 'Keep this angry face.'}],
            current_user='That was a good joke.')
        self.assertEqual(trace['status'], 'accepted')
        self.assertEqual(decision, candidate)
        self.assertTrue(client.requests[0][0]['snapshot']['face_override'])
        self.assertEqual(client.requests[0][0]['snapshot']['current_face'], face)

    def test_unsupported_tags_and_multiple_styles_preserve_face(self):
        invalid = ('excited', '[laughs]', '[warm]', 'warm and gentle',
                   'urgent; [screaming]', ['warm', 'gentle'], {'tag': 'warm'}, None)
        for delivery in invalid:
            with self.subTest(delivery=delivery):
                decision, trace, _ = self.decide(self.candidate(delivery),
                                               current_user='Please explain the next step.')
                self.assertEqual(decision, unchanged(FACE))
                self.assertEqual(trace['status'], 'invalid_decision')

    def test_spoken_text_and_audio_tag_fields_are_rejected(self):
        for field, value in (('text', 'Here is the answer.'), ('audio_tags', ['[laughs]']),
                             ('deliveries', ['warm', 'amused'])):
            with self.subTest(field=field):
                candidate = self.candidate('amused', **{field: value})
                decision, trace, _ = self.decide(candidate,
                                               current_user='Please explain the next step.')
                self.assertEqual(decision, unchanged(FACE))
                self.assertEqual(trace['status'], 'invalid_decision')

    def test_delivery_quote_must_be_exact_not_a_sentiment_paraphrase(self):
        candidate = self.candidate('reassuring', quote='I am afraid.')
        decision, trace, _ = self.decide(candidate, current_user='I feel unsure.')
        self.assertEqual(decision, unchanged(FACE))
        self.assertEqual(trace['status'], 'ungrounded_evidence')

    def test_proposed_speech_and_tool_records_cannot_ground_delivery(self):
        proposed = 'We have a reason to celebrate.'
        for record_id in ('speaker_text_proposed_unheard', 'tool_result'):
            with self.subTest(record_id=record_id):
                candidate = self.candidate('bright', evidence=[
                    {'record_id': record_id, 'quote': proposed}])
                decision, trace, client = self.decide(candidate, speaker_text=proposed,
                    records=[{'id': 'tool_result', 'role': 'tool', 'text': proposed}])
                self.assertEqual(decision, unchanged(FACE))
                self.assertEqual(trace['status'], 'ungrounded_evidence')
                request = client.requests[0][0]['snapshot']
                self.assertEqual(request['records'], [])
                self.assertEqual(request['speaker_text_proposed_unheard'], proposed)

    def test_delivery_change_cannot_bypass_face_override(self):
        candidate = self.candidate('urgent', expression='alert', variant=0, change=True)
        decision, trace, _ = self.decide(candidate, face_override=True,
                                       current_user='Please explain the next step.')
        self.assertEqual(decision, unchanged(FACE))
        self.assertEqual(trace['status'], 'face_override')

    def test_no_public_context_preserves_face_and_neutral_delivery(self):
        candidate = unchanged(FACE)
        decision, trace, _ = self.decide(candidate)
        self.assertEqual(decision, candidate)
        self.assertEqual(trace['status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
