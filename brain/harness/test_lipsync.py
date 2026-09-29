"""Provider timing, audio gating and packet boundary checks without API calls."""
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.lipsync import mouth_cues, slice_cues, spans_from_message, shape, caption_cues


class Tests(unittest.TestCase):
    def test_voiced_whitespace_is_not_an_artificial_closed_mouth(self):
        raw=struct.pack('<h',7000)*4800
        message={'alignment':{'chars':['a',' ','o'],'charStartTimesMs':[0,80,140],'charDurationsMs':[80,60,60]}}
        cues,_=mouth_cues(raw,message)
        self.assertTrue(all(c['viseme']!='rest' for c in cues if .08<=c['time']<.14),
                        'A written space is not silence when PCM still contains speech')

    def test_dental_spelling_cue_holds_both_characters_and_packet_boundary(self):
        raw = struct.pack('<h', 7000) * 4800
        message = {'alignment': {'chars': ['t', 'h', 'a'],
                   'charStartTimesMs': [0, 60, 120], 'charDurationsMs': [60, 60, 80]}}
        cues, source = mouth_cues(raw, message)
        self.assertEqual(source, 'elevenlabs_characters_audio_gated')
        self.assertTrue(all(c['viseme'] == 'TH' for c in cues if c['time'] < .12))
        self.assertEqual(slice_cues(cues, .1, .2)[0]['viseme'], 'TH')
        self.assertEqual(next(c for c in slice_cues(cues, .1, .2) if c['time'] == .02)['viseme'], 'AA')
        self.assertEqual(shape('T', 'H'), 'TH')
        self.assertEqual(shape('d', 'h'), 'TH')
        self.assertEqual(shape('t', 'a'), 'LNT')
        self.assertEqual(shape('f'), 'FV')

    def test_silent_dental_cue_stays_closed(self):
        raw = struct.pack('<h', 7000) * 2400 + b'\0\0' * 2400
        message = {'alignment': {'chars': ['t', 'h'],
                   'charStartTimesMs': [0, 100], 'charDurationsMs': [100, 100]}}
        cues, _ = mouth_cues(raw, message)
        self.assertTrue(all(c['viseme'] == 'TH' for c in cues if c['time'] < .1))
        self.assertTrue(all(c['viseme'] == 'rest' and c['amount'] == 0 for c in cues if c['time'] >= .1))

    def test_exact_boundaries_survive_packet_splitting(self):
        raw = struct.pack('<h', 7000) * 7200
        message = {'normalizedAlignment': {'chars': ['m', 'a', 'f', 'o'],
                   'charStartTimesMs': [0, 60, 140, 220], 'charDurationsMs': [60, 80, 80, 80]}}
        cues, source = mouth_cues(raw, message)
        self.assertEqual(source, 'elevenlabs_characters_audio_gated')
        self.assertEqual(next(c for c in cues if c['time'] == .06)['viseme'], 'AA')
        self.assertEqual(slice_cues(cues, .1, .2)[0]['viseme'], 'AA')
        self.assertEqual(slice_cues(cues, .1, .2)[0]['time'], 0)
        self.assertEqual(next(c for c in slice_cues(cues, .1, .2) if c['time'] == .04)['viseme'], 'FV')
        self.assertEqual(slice_cues(cues, .2, .3)[0]['viseme'], 'FV')

    def test_silence_overrides_inaccurate_character_span(self):
        raw = struct.pack('<h', 7000) * 2400 + b'\0\0' * 2400
        cues, _ = mouth_cues(raw, {'alignment': {'chars': ['a'], 'char_start_times_ms': [0], 'char_durations_ms': [200]}})
        self.assertTrue(all(c['viseme'] == 'rest' and c['amount'] == 0 for c in cues if c['time'] >= .1))
        self.assertTrue(any(c['viseme'] == 'AA' for c in cues if c['time'] < .1))

    def test_missing_or_invalid_timings_use_labelled_energy(self):
        raw = struct.pack('<h', 5000) * 2400
        for bad in (None, {}, {'chars': ['a'], 'charStartTimesMs': [float('nan')], 'charDurationsMs': [100]},
                    {'chars': ['a'], 'charStartTimesMs': [700], 'charDurationsMs': [100]},
                    {'chars': ['a'], 'charStartTimesMs': [0], 'charDurationsMs': []}):
            cues, source = mouth_cues(raw, {'alignment': bad})
            self.assertEqual(source, 'audio_energy')
            self.assertTrue(all(c['viseme'] == 'AA' for c in cues))

    def test_independent_replies_both_start_at_zero(self):
        message = {'alignment': {'chars': ['f'], 'charStartTimesMs': [0], 'charDurationsMs': [100]}}
        raw = struct.pack('<h', 6000) * 2400
        first, _ = mouth_cues(raw, message)
        second, _ = mouth_cues(raw, message)
        self.assertEqual(first, second)
        self.assertEqual(first[0]['viseme'], 'FV')
        self.assertEqual(first[0]['time'], 0)

    def test_context_alignment_clips_small_leading_overlap_without_stretching(self):
        message={'normalizedAlignment':{'chars':['o','f'],'charStartTimesMs':[975,1056],'charDurationsMs':[81,50]}}
        spans=spans_from_message(message,.2,audio_offset=.997333)
        self.assertEqual(spans[0][1],0)
        self.assertAlmostEqual(spans[1][1],.058667)
        cues,prefix,source=caption_cues(message,.2,.997333,'Pr')
        self.assertEqual(cues[0],{'time':0,'text':'Pro'})
        self.assertEqual(cues[1],{'time':.058667,'text':'Prof'})
        self.assertEqual(prefix,'Prof')
        self.assertEqual(source,'elevenlabs_normalized_alignment')

    def test_caption_carry_uses_exact_packet_boundary_and_no_guessed_text(self):
        message={'alignment':{'chars':['H','i','.'],'charStartTimesMs':[0,60,140],'charDurationsMs':[60,80,60]}}
        cues,prefix,_=caption_cues(message,.2)
        self.assertEqual(prefix,'Hi.')
        self.assertEqual(slice_cues(cues,.1,.2),[{'time':0,'text':'Hi'},{'time':.04,'text':'Hi.'}])
        self.assertEqual(caption_cues({},.2,.2,prefix),([],prefix,'unavailable'))

    def test_silent_whitespace_remains_closed(self):
        raw=struct.pack('<h',7000)*1920+b'\0\0'*1440+struct.pack('<h',7000)*1440
        message={'alignment':{'chars':['a',' ','o'],'charStartTimesMs':[0,80,140],'charDurationsMs':[80,60,60]}}
        cues,_=mouth_cues(raw,message)
        self.assertTrue(all(c['viseme']=='rest' for c in cues if .08<=c['time']<.14))


if __name__ == '__main__':
    unittest.main(verbosity=2)
