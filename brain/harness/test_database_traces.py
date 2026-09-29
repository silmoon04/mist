"""Exercise SQLite trial persistence and compatibility with JSONL history."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from duplex.database_traces import DatabaseTraceStore
from duplex.trial_traces import BufferedJournal, MAX_PAGE, TraceStore


class DatabaseTraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "traces"
        self.store = DatabaseTraceStore(self.root, secrets=["known-provider-secret"])

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_buffered_persistence_reopen_and_interruption(self):
        buffered = BufferedJournal(self.store.start({"model": "local"}))
        for index in range(103):
            buffered.record({"type": "transcript_done", "text": f"Turn {index}"})
        buffered.finish()
        trace_id = buffered.trace_id
        reopened = DatabaseTraceStore(self.root)
        try:
            journal = reopened.get(trace_id)
            self.assertEqual(journal.metadata["status"], "ended")
            self.assertEqual(journal.metadata["event_count"], 103)
            self.assertEqual([row["id"] for row in journal.export()["events"]], list(range(1, 104)))
            self.assertEqual(journal.metadata["config"]["model"], "local")
        finally:
            reopened.close()

        active = self.store.start({"model": "interrupted"})
        active.record({"type": "transcript_done", "text": "before crash"})
        recovered = DatabaseTraceStore(self.root)
        try:
            self.assertEqual(recovered.get(active.trace_id).metadata["status"], "interrupted")
        finally:
            recovered.close()

    def test_sanitizes_secrets_reasoning_and_audio_before_sqlite(self):
        journal = self.store.start({"api_key": "unknown-key", "note": "known-provider-secret"})
        journal.record({"type": "audio", "pcm": "PRIVATE_PCM", "sample_rate": 24000,
                        "authorization": "unknown-auth"})
        journal.record({"type": "response.reasoning.delta", "delta": "secret-thinking"})
        journal.record({"type": "transcript_done", "text": "Observable words"})
        journal.finish(reason="known-provider-secret")
        payload = b"".join(path.read_bytes() for path in self.root.parent.glob("sessions.sqlite3*"))
        for secret in (b"PRIVATE_PCM", b"unknown-key", b"unknown-auth", b"secret-thinking",
                       b"known-provider-secret"):
            self.assertNotIn(secret, payload)
        exported = journal.export()
        self.assertEqual(exported["events"][0]["event"]["type"], "audio_delivery")
        self.assertEqual(exported["events"][2]["event"]["text"], "Observable words")

    def test_aborted_status_and_reason_survive_reopen(self):
        journal = self.store.start({"name": "aborted"})
        journal.record({"type": "text", "text": "partial"})
        journal.finish("aborted", "user left")
        reopened = DatabaseTraceStore(self.root)
        try:
            recovered = reopened.get(journal.trace_id)
            self.assertEqual(recovered.metadata["status"], "aborted")
            self.assertEqual(recovered.metadata["reason"], "user left")
            self.assertEqual(recovered.metadata["event_count"], 1)
            with self.assertRaises(RuntimeError):
                recovered.record({"type": "late"})
        finally:
            reopened.close()

    def test_pagination_and_missing_id(self):
        self.assertIsNone(self.store.get("0" * 32))
        self.assertIsNone(self.store.get("../escape"))
        journal = BufferedJournal(self.store.start({}))
        for index in range(MAX_PAGE + 27):
            journal.record({"type": "event", "index": index})
        journal.finish()
        first = journal.since(0, 99999)
        second = journal.since(first["cursor"], 99999)
        self.assertEqual(len(first["events"]), MAX_PAGE)
        self.assertEqual(len(second["events"]), 27)
        self.assertEqual([r["id"] for r in first["events"] + second["events"]],
                         list(range(1, MAX_PAGE + 28)))
        self.assertEqual(journal.since(99999)["cursor"], MAX_PAGE + 27)

    def test_legacy_jsonl_stays_readable_and_new_sessions_are_database_only(self):
        old_store = TraceStore(self.root)
        old = old_store.start({"name": "old"})
        old.record({"type": "text", "text": "historical"})
        old.finish()
        new = self.store.start({"name": "new"})
        new.record({"type": "text", "text": "current"})
        new.finish()
        self.assertFalse((self.root / new.trace_id).exists())
        self.assertEqual(self.store.get(old.trace_id).export()["events"][0]["event"]["text"], "historical")
        self.assertEqual({item["trace_id"] for item in self.store.list_sessions()},
                         {old.trace_id, new.trace_id})


if __name__ == "__main__":
    unittest.main()
