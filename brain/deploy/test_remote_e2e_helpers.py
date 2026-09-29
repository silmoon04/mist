"""Offline checks for the opt-in remote smoke runner."""
import base64
import tempfile
from pathlib import Path
import unittest

from test_remote_e2e import Attempt, base_origin, fixture_pcm, has_reply, plan_from_catalog, public_event


class RunnerHelpers(unittest.TestCase):
    def test_checked_in_synthetic_audio_is_validated(self):
        fixture_id, name, pcm = fixture_pcm()
        self.assertEqual(fixture_id, 'mist.naturalness.v1.audio_phrase_seam')
        self.assertEqual(name, 'audio_phrase_seam-1.wav')
        self.assertGreater(len(pcm), 32000)
        self.assertEqual(len(pcm) % 2, 0)

    def test_origin_must_be_https_and_exact(self):
        self.assertEqual(base_origin('https://mist.example/'), 'https://mist.example')
        for value in ('http://mist.example', 'https://mist.example:443', 'https://mist.example/path',
                      'https://user:secret@mist.example', 'https://mist.example?x=1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                base_origin(value)

    def test_audio_payload_stays_out_of_json_and_wav_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            attempt = Attempt('offline', Path(directory) / 'attempt')
            packet = {'type': 'audio', 'sample_rate': 24000,
                      'pcm': base64.b64encode(b'\x01\x00\x02\x00').decode()}
            self.assertNotIn('pcm', public_event(packet))
            attempt.record(packet)
            report = attempt.finish({'closed_cleanly': True})
            self.assertEqual(report['received_pcm_bytes'], 4)
            self.assertIn('pcm_bytes', (attempt.out / 'attempt.json').read_text())
            self.assertNotIn(packet['pcm'], (attempt.out / 'attempt.json').read_text())
            self.assertTrue((attempt.out / 'received.wav').exists())

    def test_assistant_reply_requires_final_nonempty_text(self):
        rows = [{'type': 'transcript_delta', 'role': 'assistant', 'text': 'hello'},
                {'type': 'transcript_done', 'role': 'user', 'text': 'hello'}]
        self.assertFalse(has_reply(rows))
        rows.append({'type': 'transcript_done', 'role': 'assistant', 'text': 'The blue bracket fits.'})
        self.assertTrue(has_reply(rows))
        self.assertTrue(has_reply(rows, topic=('bracket',)))
        self.assertFalse(has_reply(rows, topic=('washer',)))
        self.assertFalse(has_reply(rows, after=3))

    def test_architectures_are_available_and_distinct_before_any_turn(self):
        catalog = {'qwen-affect': {'available': True}, 'qwen-low': {'available': True},
                   'cerebras-balanced': {'available': False}}
        plan = plan_from_catalog(catalog, 'qwen-affect', 'qwen-low', b'pcm')
        self.assertEqual([case[0] for case in plan],
                         ['qwen-affect', 'qwen-affect', 'qwen-affect', 'qwen-low'])
        self.assertEqual(len(plan), 4)
        for primary, alternate in [('qwen-affect', 'missing'), ('cerebras-balanced', 'qwen-low'),
                                   ('qwen-low', 'qwen-low')]:
            with self.subTest(primary=primary, alternate=alternate), self.assertRaises(ValueError):
                plan_from_catalog(catalog, primary, alternate, b'pcm')


if __name__ == '__main__':
    unittest.main()
