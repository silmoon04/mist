"""Browser caption receipts must be grounded in this socket's sent audio."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.playback_receipts import PlaybackReceipts


class Tests(unittest.TestCase):
    def setUp(self):
        self.receipts = PlaybackReceipts()
        self.receipts.observe_audio({'type': 'audio', 'epoch': 2, 'seq': 7,
            'caption_source': 'elevenlabs_alignment',
            'caption_cues': [{'time': 0, 'text': 'Hello'},
                             {'time': .2, 'text': 'Hello there'}]})

    def event(self, text, **changes):
        return {'type': 'caption_progress', 'epoch': 2, 'sequence': 7,
                'text': text, 'source': 'elevenlabs_alignment', **changes}

    def test_accepts_only_sent_monotonic_prefixes(self):
        first = self.receipts.accept(self.event('Hello', client_ms=12.5))
        self.assertEqual(first['text'], 'Hello')
        self.assertEqual(first['basis'], 'client_reported_caption_display')
        self.assertFalse(first['complete'])
        self.assertEqual(first['client_ms'], 12.5)
        self.assertIsNone(self.receipts.accept(self.event('Hello')))
        self.assertIsNone(self.receipts.accept(self.event('Hello there!')))
        self.assertEqual(self.receipts.accept(self.event('Hello there'))['text'], 'Hello there')
        self.assertIsNone(self.receipts.accept(self.event('Hello')))
        self.assertIn('audio hearing unverified', self.receipts.context())

    def test_rejects_stale_epoch_source_and_unsent_or_unauthorized_sequence(self):
        self.assertIsNone(self.receipts.accept(self.event('Hello', epoch=1)))
        self.assertIsNone(self.receipts.accept(self.event('Hello', sequence=8)))
        self.assertIsNone(self.receipts.accept(self.event('Hello', source='unavailable')))
        self.assertIsNone(self.receipts.accept(self.event('other')))
        self.receipts.reset(3)
        self.assertIsNone(self.receipts.accept(self.event('Hello')))
        self.assertIsNone(self.receipts.accept(self.event('Hello', epoch=True)))

    def test_interrupted_prefix_stays_partial_and_new_sequence_cannot_regress(self):
        self.assertEqual(self.receipts.accept(self.event('Hello'))['text'], 'Hello')
        self.receipts.observe_audio({'type': 'audio', 'epoch': 2, 'seq': 8,
            'caption_source': 'elevenlabs_alignment',
            'caption_cues': [{'time': 0, 'text': 'Next'}]})
        self.assertEqual(self.receipts.accept(self.event('Next', sequence=8))['sequence'], 8)
        self.assertIsNone(self.receipts.accept(self.event('Hello there')))
        self.receipts.reset(3)
        self.assertIn('Next', self.receipts.context())


if __name__ == '__main__':
    unittest.main()
