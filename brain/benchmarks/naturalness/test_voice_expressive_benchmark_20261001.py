"""Offline checks for the bounded Eleven voice pilot."""
import asyncio
import base64
from collections import Counter
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import voice_expressive_20261001 as pilot


class FakeTTS:
    fail_mode = None

    def __init__(self, key, emit, model_id, voice_id):
        self.emit = emit
        self.model_id = model_id
        self.voice_id = voice_id
        self.active = None
        self.pending = {}
        self.muted = False
        self.parts = []

    async def start(self):
        return None

    async def begin(self, clip_id, delivery):
        self.active = clip_id
        self.pending[clip_id] = True
        await self.emit({"type": "speech_style", "delivery": delivery, "voice_id": self.voice_id,
                         "model": self.model_id, "phase": "committed", "tag_name": None})

    async def text(self, part):
        self.parts.append(part)

    async def finish(self, transcript):
        assert transcript == "".join(self.parts)
        context = self.active
        await self.emit({"type": "latency", "tts": {"output_s": .1 if self.fail_mode != "mismatch" else .2}})
        await asyncio.sleep(.03)  # Provider final precedes the drained PCM in the adapter.
        if self.fail_mode != "empty":
            raw = b"\x00\x00" * 2400
            await self.emit({"type": "audio", "pcm": base64.b64encode(raw).decode(),
                             "alignment_source": "elevenlabs_characters_audio_gated",
                             "caption_source": "elevenlabs_normalized_alignment",
                             "mouth_cues": [{"time": 0}],
                             "caption_cues": [{"time": 0, "text": "[warmly] hi" if self.fail_mode == "tag" else "hi"}]})
        self.pending.pop(context)

    async def close(self):
        return None


class PilotTests(unittest.TestCase):
    def test_frozen_schedule_and_budget(self):
        rows = pilot.schedule()
        self.assertEqual(rows, pilot.schedule())
        self.assertEqual(len(rows), 30)
        self.assertEqual(pilot.submitted_characters(rows), 2663)
        self.assertLess(pilot.submitted_characters(rows), 5000)
        counts = Counter((row["id"], row["voice_name"], row["model"]) for row in rows)
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertEqual(sum(row["model"] == pilot.FLASH_MODEL for row in rows), 2)

    def test_listen_page_reveals_voice_and_latency_only_after_click(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "listen.html"
            pilot.write_listen_page([{"clip_id": "clip-01", "case_id": "neutral",
                                      "wav_file": "clip-01.wav", "voice_name": "Jessica",
                                      "voice_id": pilot.VOICES["Jessica"], "cold_connect_s": 2.5}], path)
            page = path.read_text(encoding="utf-8")
            self.assertIn('src="audio/clip-01.wav"', page)
            self.assertIn('class="reveal"', page)
            self.assertIn('.reveal{display:none}', page)
            self.assertIn('document.body.classList.add("revealed")', page)
            self.assertIn("Jessica", page)
            self.assertNotIn(pilot.VOICES["Jessica"], page)
            self.assertIn("Export ratings", page)
            self.assertIn('class="naturalness"', page)
            self.assertIn('class="overacting"', page)

    def test_completion_requires_pcm_duration_and_clean_captions(self):
        sample = next(row for row in pilot.schedule() if row["id"] == "empathy")

        async def render(mode):
            FakeTTS.fail_mode = mode
            with tempfile.TemporaryDirectory() as temp:
                with patch.object(pilot, "StreamingTTS", FakeTTS):
                    return await pilot.render_one("fixture-key", sample, "clip-01", Path(temp))

        try:
            ok = asyncio.run(render(None))
            self.assertEqual(ok["status"], "ok")
            self.assertTrue(ok["provider_final"])
            self.assertTrue(ok["pending_drained"])
            self.assertEqual(ok["audio_bytes"], 4800)
            self.assertEqual(ok["mouth_cue_count"], 1)
            self.assertEqual(ok["caption_cue_count"], 1)
            for mode in ("empty", "mismatch", "tag"):
                with self.subTest(mode=mode):
                    row = asyncio.run(render(mode))
                    self.assertEqual(row["status"], "incomplete_or_tag_leak")
        finally:
            FakeTTS.fail_mode = None


if __name__ == "__main__":
    unittest.main()
