import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from duplex.affect_director import (AffectDirector, DIRECTOR_PROMPT, _snapshot,
                                    fast_delivery_fallback, validate_decision)


FACE = {'expression': 'curious', 'variant': 0}
SNAPSHOT = {'records': [{'id': 'r1', 'role': 'user', 'text': 'I finally got the robot walking!'}],
            'current_user': 'I finally got the robot walking!', 'current_face': FACE}


class FakeClient:
    def __init__(self, answer, **options):
        self.answer = answer
        self.options = options
        self.closed = False
        self.requests = []
        self._specs = ['must be cleared']
        self._selected = {'must be cleared'}
        self._bridge = object()

    def new_session(self):
        pass

    def ask(self, request, timeout):
        self.requests.append((json.loads(request), timeout))
        return SimpleNamespace(text=self.answer, errors=[], tool_calls=[], input_tokens=123,
                               output_tokens=45)

    def close(self):
        self.closed = True


class AffectDirectorTests(unittest.TestCase):
    def test_fast_delivery_fallback_uses_current_turn_and_keeps_manual_face_out_of_it(self):
        self.assertEqual(fast_delivery_fallback('What is the next step?',
                         {'expression': 'happy', 'variant': 0}), 'warm')
        self.assertEqual(fast_delivery_fallback('Thanks, that was helpful.'), 'warm')

    def test_fast_delivery_fallback_honors_explicit_voice_direction(self):
        self.assertEqual(fast_delivery_fallback('Use a neutral voice.'), 'neutral')
        self.assertEqual(fast_delivery_fallback('Please speak gently.'), 'gentle')
        self.assertEqual(fast_delivery_fallback('Use a serious voice.'), 'serious')
        self.assertEqual(fast_delivery_fallback('Be cheerful about this.'), 'bright')

    def test_fast_delivery_fallback_does_not_perform_negated_directions(self):
        for text in ("Don't speak cheerfully.", 'Do not use a bright voice.',
                     'Never be cheerful about bad news.', 'Avoid speaking softly.'):
            self.assertEqual(fast_delivery_fallback(text),'neutral')

    def test_fast_delivery_fallback_uses_restrained_empathy_for_bad_news(self):
        self.assertEqual(fast_delivery_fallback('My dog passed away yesterday.'), 'gentle')
        self.assertEqual(fast_delivery_fallback('There is an emergency; someone is hurt.'), 'serious')

    def reader(self, answer):
        clients = []

        def factory(**options):
            client = FakeClient(answer, **options)
            clients.append(client)
            return client

        return AffectDirector(client_factory=factory), clients

    def test_valid_grounded_change_and_no_execution_authority(self):
        answer = json.dumps({'change': True, 'expression': 'happy', 'variant': 0,
                             'delivery': 'bright',
                             'evidence': [{'record_id': 'current_user', 'quote': 'got the robot walking'}]})
        director, clients = self.reader(answer)
        decision, trace = director.decide_with_trace(SNAPSHOT)
        self.assertTrue(decision['change'])
        self.assertEqual(decision['expression'], 'happy')
        self.assertEqual(trace['status'], 'accepted')
        client = clients[0]
        self.assertEqual(client._specs, [])
        self.assertEqual(client._selected, set())
        self.assertIsNone(client._bridge)
        self.assertTrue(client.closed)
        self.assertEqual(client.options['system_prompt'], DIRECTOR_PROMPT)
        self.assertEqual(client.options['max_output_tokens'], 1024)
        self.assertEqual(trace['raw_decision'], answer)
        self.assertNotIn('result', trace)

    def test_large_snapshot_preserves_current_user_and_speaker_within_budget(self):
        snapshot = {'records': [{'id': f'r{i}', 'role': 'user', 'text': 'R' * 900}
                                for i in range(12)],
                    'current_user': 'U' * 900, 'speaker_text': 'S' * 900,
                    'current_face': FACE}
        normalized, sources = _snapshot(snapshot)
        total = sum(len(record['text']) for record in normalized['records'])
        total += len(normalized['speaker_text_proposed_unheard'])
        self.assertLessEqual(total, 6000)
        self.assertEqual(sources['current_user'], 'U' * 900)
        self.assertEqual(normalized['speaker_text_proposed_unheard'], 'S' * 900)
        self.assertEqual(normalized['records'][-2]['record_id'], 'r11')

    def test_unchanged_face_keeps_variant(self):
        answer = json.dumps({'change': False, 'expression': 'curious', 'variant': 0,
                             'delivery': 'neutral', 'evidence': []})
        director, _ = self.reader(answer)
        self.assertEqual(director.decide(SNAPSHOT)['expression'], 'curious')

    def test_malformed_and_unavailable_faces_preserve_current_face(self):
        for answer in ('{broken', json.dumps({'change': True, 'expression': 'made_up',
                                              'variant': 0, 'delivery': 'warm', 'evidence': []}),
                       json.dumps({'change': True, 'expression': 'happy', 'variant': 9,
                                   'delivery': 'bright', 'evidence': []})):
            with self.subTest(answer=answer):
                director, clients = self.reader(answer)
                decision = director.decide(SNAPSHOT)
                self.assertEqual(decision, {'change': False, **FACE,
                                            'delivery': 'neutral', 'evidence': []})
                self.assertTrue(clients[0].closed)

    def test_hallucinated_quote_is_rejected(self):
        answer = json.dumps({'change': True, 'expression': 'happy', 'variant': 0,
                             'delivery': 'bright',
                             'evidence': [{'record_id': 'r1', 'quote': 'won the competition'}]})
        director, _ = self.reader(answer)
        decision, trace = director.decide_with_trace(SNAPSHOT)
        self.assertFalse(decision['change'])
        self.assertEqual(trace['status'], 'ungrounded_evidence')

    def test_proposed_speech_cannot_be_cited_as_public_evidence(self):
        answer = json.dumps({'change': True, 'expression': 'happy', 'variant': 0,
                             'delivery': 'bright',
                             'evidence': [{'record_id': 'speaker_text', 'quote': 'I am delighted'}]})
        director, _ = self.reader(answer)
        decision, trace = director.decide_with_trace({**SNAPSHOT, 'speaker_text': 'I am delighted'})
        self.assertFalse(decision['change'])
        self.assertEqual(trace['status'], 'ungrounded_evidence')

    def test_explicit_face_override_allows_grounded_delivery_but_no_face_change(self):
        answer = json.dumps({'change': False, 'expression': 'sad', 'variant': 0,
                             'delivery': 'gentle',
                             'evidence': [{'record_id': 'current_user',
                                           'quote': 'speak more gently'}]})
        director, clients = self.reader(answer)
        decision, trace = director.decide_with_trace({**SNAPSHOT,
            'current_user': 'Keep the sad face but speak more gently.',
            'current_face': {'expression': 'sad', 'variant': 0}, 'face_override': True})
        self.assertEqual(trace['status'], 'accepted')
        self.assertEqual(decision['delivery'], 'gentle')
        self.assertEqual(decision['expression'], 'sad')
        self.assertFalse(decision['change'])
        self.assertEqual(len(clients), 1)

    def test_explicit_face_override_rejects_model_face_change(self):
        answer = json.dumps({'change': True, 'expression': 'happy', 'variant': 0,
                             'delivery': 'warm',
                             'evidence': [{'record_id': 'current_user', 'quote': 'got the robot walking'}]})
        director, _ = self.reader(answer)
        decision, trace = director.decide_with_trace({**SNAPSHOT, 'face_override': True})
        self.assertFalse(decision['change'])
        self.assertEqual(trace['status'], 'face_override')

    def test_non_neutral_delivery_requires_evidence(self):
        candidate = {'change': False, **FACE, 'delivery': 'warm', 'evidence': []}
        with self.assertRaisesRegex(ValueError, 'missing_evidence'):
            validate_decision(candidate, current_face=FACE, sources={})

    def test_failed_client_is_closed_and_trace_hides_error(self):
        class Failure(FakeClient):
            def ask(self, request, timeout):
                raise RuntimeError('secret response text')

        clients = []

        def factory(**options):
            client = Failure('', **options)
            clients.append(client)
            return client

        director = AffectDirector(client_factory=factory)
        decision, trace = director.decide_with_trace(SNAPSHOT)
        self.assertFalse(decision['change'])
        self.assertEqual(trace['status'], 'provider_failure')
        self.assertNotIn('secret', json.dumps(trace))
        self.assertTrue(clients[0].closed)

    def test_provider_failure_retains_only_public_bounded_diagnostics(self):
        class FailedResult(FakeClient):
            def ask(self, request, timeout):
                return SimpleNamespace(text='X' * 5000, errors=['Cerebras HTTP 429',
                                       'Bearer secret token'], tool_calls=[], input_tokens=17,
                                       output_tokens=1024, ttft_s=.12, total_s=1.3,
                                       timings={'http_requests': [{'status': 429, 'raw': 'secret token'}],
                                                'private_reasoning': 'secret token'})

        director = AffectDirector(client_factory=lambda **options: FailedResult('', **options))
        decision, trace = director.decide_with_trace(SNAPSHOT)
        self.assertFalse(decision['change'])
        self.assertEqual(trace['status'], 'provider_failure')
        self.assertEqual(trace['error_codes'], ['http_429', 'provider_error'])
        self.assertEqual(trace['http_statuses'], [429])
        self.assertEqual(trace['input_tokens'], 17)
        self.assertEqual(trace['output_tokens'], 1024)
        self.assertEqual(trace['ttft_ms'], 120)
        self.assertEqual(len(trace['raw_decision']), 4000)
        self.assertNotIn('secret token', json.dumps(trace))


if __name__ == '__main__':
    unittest.main()
