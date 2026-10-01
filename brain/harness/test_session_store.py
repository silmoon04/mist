"""Offline durability and provenance checks for the local MIST ledger."""

import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from brain.duplex.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "private.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_restart_preserves_originals_search_and_provenance(self):
        with SessionStore(self.db) as store:
            store.start_session("room-1", {"title": "Local session", "api_key": "secret"})
            store.append_events("room-1", [
                {"id": 1, "source": "server", "event": {"type": "turn.done", "text": "one"}},
                {"id": 2, "source": "server", "event": {"type": "turn.done", "text": "two"}},
            ])
            first = store.append_turn("room-1", "user", "Ticket number forty two", source_event_id="1")
            reply = store.append_turn("room-1", "assistant", "I heard forty two, yes.", source_event_id="2")
            self.assertFalse(reply["playback_verified"])
            partial = store.mark_turn_played(reply["id"], "I heard")
            self.assertFalse(partial["playback_verified"])
            store.save_summary("room-1", "Ticket discussion", ["1", "2"])
            store.save_note("room-1", "Correction: forty three", ["1"], correction_of=None)
        with SessionStore(self.db) as store:
            self.assertEqual(store.get_session("room-1")["metadata"]["api_key"], "[redacted]")
            self.assertEqual([e["payload"]["id"] for e in store.list_events("room-1", after_id=1)], [2])
            self.assertEqual([e["payload"]["id"] for e in store.list_events(
                "room-1", after_source_event_id=1, limit=1)], [2])
            self.assertEqual(store.search_turns('"forty two"', "room-1")[0]["id"], first["id"])
            self.assertEqual(len(store.list_turns("room-1")), 2)
            self.assertEqual(store.list_summaries("room-1")[0]["source_event_ids"], ["1", "2"])
            self.assertEqual(store.list_notes("room-1")[0]["source_event_ids"], ["1"])
            self.assertEqual(store.get_turn(reply["id"])["played_text"], "I heard")

    def test_audio_wav_offsets_restart_and_format_guard(self):
        a = struct.pack("<4h", 1, -2, 3, -4)
        b = struct.pack("<2h", 5, -6)
        with SessionStore(self.db) as store:
            self.assertTrue(store.enqueue_audio("room-1", "mic", a, sample_rate=16000,
                                                timestamp_ms=100, metadata={"seq": 1, "token": "secret"}))
            self.assertTrue(store.enqueue_audio("room-1", "mic", b, sample_rate=16000,
                                                timestamp_ms=101, metadata={"seq": 2}))
            store.flush_audio()
            self.assertEqual(store.audio_status()["errors"], [])
            rows = store.list_audio_chunks("room-1", "mic")
            self.assertEqual([(r["offset_bytes"], r["byte_count"]) for r in rows], [(0, len(a)), (len(a), len(b))])
            self.assertEqual(rows[0]["metadata"]["token"], "[redacted]")
            timeline = store.audio_timeline("room-1")
            self.assertEqual([(r["stream"], r["timestamp_ms"], r["offset_bytes"])
                              for r in timeline], [("mic", 100, 0), ("mic", 101, len(a))])
            self.assertEqual(timeline[0]["metadata"]["token"], "[redacted]")
            self.assertNotIn("path", timeline[0])
            summary = store.audio_summary("room-1")
            self.assertEqual(len(summary["streams"]), 1)
            self.assertEqual(summary["streams"][0]["chunk_count"], 2)
            self.assertEqual(summary["streams"][0]["byte_count"], len(a) + len(b))
            self.assertEqual(summary["streams"][0]["sample_rate"], 16000)
            self.assertTrue(summary["streams"][0]["exists"])
            self.assertEqual(summary["streams"][0]["duration_ms"], 0)
            path = store.audio_path("room-1", "mic")
            with wave.open(str(path), "rb") as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
                self.assertEqual(wav.readframes(100), a + b)
        with SessionStore(self.db) as store:
            self.assertTrue(store.enqueue_audio("room-1", "mic", a, sample_rate=16000))
            self.assertTrue(store.enqueue_audio("room-1", "assistant_generated", b, sample_rate=24000))
            self.assertTrue(store.enqueue_audio("room-1", "mic", b, sample_rate=48000))
            store.flush_audio()
            self.assertTrue(any("format changed" in error for error in store.audio_status()["errors"]))
            with wave.open(str(store.audio_path("room-1", "mic")), "rb") as wav:
                self.assertEqual(wav.readframes(100), a + b + a)
            self.assertEqual(len(store.list_audio_chunks("room-1", "mic")), 3)
            self.assertEqual(len(store.list_audio_chunks("room-1", "assistant_generated")), 1)
            self.assertEqual({row["stream"] for row in store.audio_summary("room-1")["streams"]},
                             {"mic", "assistant_generated"})

    def test_invalid_paths_and_binary_event_payload_rejected(self):
        with SessionStore(self.db) as store:
            with self.assertRaises(ValueError):
                store.enqueue_audio("../escape", "mic", b"\0\0", sample_rate=16000)
            with self.assertRaises(ValueError):
                store.append_event("s", "audio", {"raw_audio": b"bytes"})
            with self.assertRaises(ValueError):
                store.enqueue_audio("s", "mic", b"\0", sample_rate=16000)
            with self.assertRaises(ValueError):
                store.enqueue_audio("s", "mic", b"RIFF\0\0\0\0", sample_rate=16000)
            self.assertFalse((Path(self.temp.name).parent / "escape").exists())

    def test_session_listing_counts_live_events(self):
        with SessionStore(self.db) as store:
            store.start_session("live")
            store.append_events("live", [
                {"id": 4, "event": {"type": "one"}},
                {"id": 8, "event": {"type": "two"}},
            ])
            row = store.list_sessions()[0]
            self.assertEqual((row["event_count"], row["event_head"]), (2, 8))

    def test_pcm_sample_resembling_mp3_sync_is_recorded(self):
        # A PCM sample can begin with the same two bytes as an MP3 frame.
        with SessionStore(self.db) as store:
            for prefix in (b"\xff\xfb", b"\xff\xf3"):
                self.assertTrue(store.enqueue_audio("s", "mic", prefix + b"\0\0" * 100,
                                                    sample_rate=16000))
            store.flush_audio()
            self.assertEqual(len(store.list_audio_chunks("s", "mic")), 2)

    def test_slow_audio_disk_does_not_hold_ledger_lock(self):
        entered = threading.Event()

        def slow_fsync(_):
            entered.set()
            time.sleep(0.25)

        with SessionStore(self.db) as store, patch("brain.duplex.session_store.os.fsync", slow_fsync):
            store.enqueue_audio("s", "mic", b"\0\0" * 100, sample_rate=16000)
            self.assertTrue(entered.wait(2))
            started = time.monotonic()
            store.append_turn("s", "user", "This write should not wait for the audio disk")
            self.assertLess(time.monotonic() - started, 0.15)
            store.flush_audio()
            self.assertEqual(store.audio_status()["errors"], [])

    def test_preference_survives_new_session_and_restart(self):
        exact = "Please call the robot MIST, in capitals."
        with SessionStore(self.db) as store:
            store.start_session("first")
            preference_id = store.save_preference(exact, "first", "event-7")
            store.finish_session("first")
        with SessionStore(self.db) as store:
            store.start_session("second")
            self.assertEqual(store.list_preferences(), [{
                "id": preference_id, "created_ms": store.list_preferences()[0]["created_ms"],
                "text": exact, "session_id": "first", "source_event_id": "event-7"}])
            self.assertEqual(store.list_preferences(limit=0), [])

    def test_enqueue_close_race_cannot_accept_after_writer_exits(self):
        store = SessionStore(self.db)
        entered = threading.Event()
        release = threading.Event()
        accepted = []
        original = store._audio_queue.put_nowait

        def delayed_put(item):
            entered.set()
            self.assertTrue(release.wait(2))
            return original(item)

        with patch.object(store._audio_queue, "put_nowait", delayed_put):
            enqueue = threading.Thread(target=lambda: accepted.append(
                store.enqueue_audio("race", "mic", b"\0\0", sample_rate=16000)))
            enqueue.start()
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=store.close)
            closer.start()
            self.assertTrue(closer.is_alive())
            release.set()
            enqueue.join(2)
            closer.join(2)
        self.assertFalse(enqueue.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(accepted, [True])
        with SessionStore(self.db) as reopened:
            self.assertEqual(len(reopened.list_audio_chunks("race", "mic")), 1)


if __name__ == "__main__":
    unittest.main()
