import unittest
from duplex.review_data import build_session_review


class ReviewTests(unittest.TestCase):
    def review(self, events=None, chunks=None, turns=None):
        return build_session_review({'session': {'trace_id': 'abc', 'config': {'id': 'qwen-memory'}},
            'events': events or []}, turns or [], chunks or [])

    def test_generated_offsets_are_separate_from_wall_clock(self):
        chunks = [dict(stream='assistant_generated', timestamp_ms=5000 + n * 10,
            offset_bytes=n * 48000, byte_count=48000, sample_rate=24000, channels=1, metadata={'epoch': 0}) for n in range(3)]
        result = self.review(chunks=chunks)
        segment = result['audio'][0]['segments'][0]
        self.assertEqual(segment['audio_start_ms'], 0)
        self.assertEqual(segment['audio_end_ms'], 3000)
        self.assertEqual(segment['start_ms'], 5000)
        self.assertEqual(segment['end_ms'], 8000)
        self.assertEqual(result['audio'][0]['clock'], 'generated_receipt')

    def test_epoch_boundaries_never_merge(self):
        chunks = [dict(stream='assistant_generated', timestamp_ms=10, offset_bytes=n * 48000,
            byte_count=48000, sample_rate=24000, channels=1, metadata={'epoch': n}) for n in range(2)]
        self.assertEqual(len(self.review(chunks=chunks)['audio'][0]['segments']), 2)

    def test_distinct_replies_with_one_epoch_keep_audio_and_captions_separate(self):
        chunks = [dict(stream='assistant_generated', timestamp_ms=10 + n * 1000,
            offset_bytes=n * 48000, byte_count=48000, sample_rate=24000, channels=1,
            metadata={'epoch': 0, 'turn_id': f'turn-{n}', 'seq': n}) for n in range(2)]
        events = []
        for n, text in enumerate(('First answer.', 'Second answer.')):
            events.extend([
                dict(id=n * 2, elapsed_ms=n * 1000, event=dict(type='transcript_done',
                    role='assistant', text=text, turn_id=f'turn-{n}')),
                dict(id=n * 2 + 1, elapsed_ms=n * 1000 + 400, event=dict(type='caption_progress',
                    epoch=0, sequence=n, turn_id=f'turn-{n}', text=text))])
        result = self.review(events, chunks, [dict(id=n, role='assistant', text=text)
            for n, text in enumerate(('First answer.', 'Second answer.'))])
        self.assertEqual(len(result['audio'][0]['segments']), 2)
        self.assertEqual([turn['displayed_caption'] for turn in result['turns']],
                         ['First answer.', 'Second answer.'])
        self.assertEqual(result['turns'][1]['audio']['start_ms'], 1000)

    def test_generated_unheard_tail_does_not_extend_session(self):
        events = [dict(id=1, elapsed_ms=1500, event=dict(type='audio_reset', epoch=1))]
        chunks = [dict(stream='assistant_generated', timestamp_ms=1000, offset_bytes=0,
            byte_count=480000, sample_rate=24000, channels=1, metadata={'epoch': 0})]
        self.assertEqual(self.review(events, chunks)['duration_ms'], 1500)

    def test_missing_words_are_flagged_but_prefix_interrupt_is_not(self):
        events = [dict(id=1, elapsed_ms=10, event=dict(type='transcript_done', role='assistant', text='Tomorrow is a fresh page.')),
                  dict(id=2, elapsed_ms=100, event=dict(type='caption_progress', epoch=0, sequence=0, text='is a page.'))]
        chunks = [dict(stream='assistant_generated', timestamp_ms=20, offset_bytes=0,
            byte_count=48000, sample_rate=24000, channels=1, metadata={'epoch': 0})]
        turns = [dict(id=1, role='assistant', text='Tomorrow is a fresh page.')]
        result = self.review(events, chunks, turns)
        self.assertTrue(any(issue['id'] == 'caption-1' for issue in result['issues']))
        events[1]['event']['text'] = 'Tomorrow is'
        self.assertFalse(self.review(events, chunks, turns)['issues'])

    def test_only_bounded_events_and_no_local_paths(self):
        events = [dict(id=n, elapsed_ms=n, event=dict(type='state', state='listening')) for n in range(2000)]
        result = self.review(events)
        self.assertEqual(len(result['events']), 500)
        self.assertEqual(result['event_count'], 2000)
        self.assertEqual(result['events'][0]['id'], 1500)


if __name__ == '__main__':
    unittest.main()
