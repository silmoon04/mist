import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runner


class FakeClient:
    made = []

    def __init__(self, **kwargs):
        self.kwargs, self.sent, self.closed = kwargs, [], False
        self.__class__.made.append(self)

    def new_session(self):
        self.session_started = True

    def ask(self, prompt, timeout=45):
        self.sent.append(prompt)
        return SimpleNamespace(text="Acknowledged.", errors=[], tool_calls=[], ttft_s=0.02)

    def close(self):
        self.closed = True


class RobustConversationRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite, cls.raw, cls.digest = runner.load_suite()

    def case(self, cid):
        return next(c for c in self.suite["cases"] if c["id"] == cid)

    def test_frozen_suite_integrity_and_partition(self):
        self.assertEqual(len(self.suite["cases"]), 30)
        self.assertEqual(self.digest, runner.FROZEN_CASES_SHA256)
        self.assertEqual({s: sum(c["split"] == s for c in self.suite["cases"])
                          for s in ("development", "heldout")}, {"development": 15, "heldout": 15})
        families = {}
        for case in self.suite["cases"]:
            families.setdefault(case["scenario_family"], set()).add(case["split"])
            self.assertTrue(case["failure_seeds"], case["id"])
        self.assertTrue(all(len(v) == 1 for v in families.values()))

    def test_hash_drift_fails_closed(self):
        temp = HERE / "_temporary_hash_drift.json"
        try:
            temp.write_bytes(self.raw + b" ")
            with self.assertRaisesRegex(ValueError, "Frozen cases hash mismatch"):
                runner.load_suite(temp)
        finally:
            temp.unlink(missing_ok=True)

    def test_text_adapter_rejects_missing_evidence(self):
        self.assertIsNone(runner.unsupported_reason(self.case("mist.robust.v1.epistemic.hypothetical.dev1")))
        self.assertIsNone(runner.unsupported_reason(self.case("mist.robust.v1.asr.repair_entity.dev1")))
        self.assertIn("audio", runner.unsupported_reason(self.case("mist.robust.v1.floor.clause_continuation.dev1")))
        self.assertIn("memory", runner.unsupported_reason(self.case("mist.robust.v1.memory.implicit_relevant.dev1")))
        self.assertIn("background task", runner.unsupported_reason(self.case("mist.robust.v1.background.same_intent.held1")))
        self.assertIn("audio", runner.unsupported_reason(self.case("mist.robust.v1.voice.serious_content.held1")))

    def test_live_case_uses_real_session_history_and_stays_unrated(self):
        FakeClient.made.clear()
        case = self.case("mist.robust.v1.epistemic.hypothetical.dev1")
        config = {"model": "qwen-3.8-27b", "reasoning": "low", "max_output_tokens": 100, "timeout_s": 1}
        sample = runner.run_case(case, 1, "a", config, "system", FakeClient)
        self.assertEqual(sample["status"], "completed")
        self.assertTrue(sample["case_complete"])
        self.assertEqual(sample["semantic_judgment"], "unrated")
        client = FakeClient.made[0]
        self.assertTrue(client.session_started)
        self.assertTrue(client.closed)
        self.assertEqual(client.sent, [x["user_text"] for x in runner.user_events(case)])

    def test_blind_packet_is_deterministic_and_hides_arm_names(self):
        case = self.case("mist.robust.v1.epistemic.hypothetical.dev1")
        samples = [{"case_id": case["id"], "repeat": 1, "arm": arm, "status": "completed",
                    "case_complete": True, "turns": [{"user_text": "Question.", "text": text}]}
                   for arm, text in (("a", "Answer one."), ("b", "Answer two."))]
        data = {"suite_id": self.suite["suite_id"], "suite_version": self.suite["version"],
                "suite_sha256": self.digest, "arms": {"a": {"model": "secret-A"},
                "b": {"model": "secret-B"}}, "samples": samples}
        p1, key1 = runner.build_blind_packet(data, [case], 7)
        p2, key2 = runner.build_blind_packet(data, [case], 7)
        self.assertEqual(p1, p2)
        self.assertEqual(key1, key2)
        self.assertNotIn("secret-A", str(p1))
        self.assertNotIn("secret-B", str(p1))
        self.assertEqual(p1["review_status"], "unrated_pending_independent_review")
        self.assertIsNone(p1["pairs"][0]["ratings"]["preference"])

    def test_blind_packet_omits_failed_pairs(self):
        case = self.case("mist.robust.v1.epistemic.hypothetical.dev1")
        samples = [{"case_id": case["id"], "repeat": 1, "arm": arm, "status": status,
                    "case_complete": status == "completed", "turns": []}
                   for arm, status in (("a", "completed"), ("b", "failed"))]
        data = {"suite_id": self.suite["suite_id"], "suite_version": self.suite["version"],
                "suite_sha256": self.digest, "arms": {"a": {}, "b": {}}, "samples": samples}
        packet, key = runner.build_blind_packet(data, [case], 7)
        self.assertEqual(packet["pairs"], [])
        self.assertEqual(key["pairs"], [])

    def test_case_selection_respects_split(self):
        selected = runner.select_cases(self.suite, "development", ["mist.robust.v1.epistemic.hypothetical.dev1"])
        self.assertEqual(len(selected), 1)
        with self.assertRaisesRegex(ValueError, "out-of-split"):
            runner.select_cases(self.suite, "development", ["mist.robust.v1.background.same_intent.held1"])


if __name__ == "__main__":
    unittest.main()
