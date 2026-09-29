"""Offline app-server stream fixtures with the real robot tool bridge."""
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import codex_client as adapter


class FixtureServer:
    def __init__(self):
        self.events = queue.Queue()
        self.proc = self
        self.requests = []
        self.thread = 0
        self.turn = 0
        self.pending_tool = None
        self.closed = False

    def poll(self):
        return 0 if self.closed else None

    def request(self, method, params=None, **kwargs):
        params = params or {}
        self.requests.append((method, params))
        if method == "model/list":
            return {"data": []}
        if method == "thread/start":
            self.thread += 1
            self.turn = 0
            return {"thread": {"id": str(self.thread)}, "model": params["model"],
                    "reasoningEffort": "low", "serviceTier": params["serviceTier"]}
        if method == "turn/start":
            self.turn += 1
            prompt = params["input"][0]["text"]
            if prompt == "start_timeout":
                raise TimeoutError("turn/start acknowledgement timed out")
            if prompt == "chat_metadata":
                self.complete_message("[[face:happy]] Hello, Moon.", ["[[fa", "ce:happy]] ", "Hello, Moon."])
                return {"turn": {"id": str(self.turn)}}
            if prompt == "web":
                self.notify("item/started", {"item": {"type": "webSearch", "id": "search-1", "query": "fixture"}})
                self.notify("item/completed", {"item": {"type": "webSearch", "id": "search-1", "query": "fixture"}})
                self.complete_message("Verified source.", ["Verified source."])
                return {"turn": {"id": str(self.turn)}}
            # A previous turn's valid-looking tool request must not execute.
            self.notify("item/agentMessage/delta", {"turnId": "stale", "itemId": "old", "delta": "wrong"})
            if prompt == "retry":
                self.notify("error", {"error": {"message": "Temporary fixture transport error"}, "willRetry": True})
            if prompt not in ("timeout", "cancel"):
                name, arguments = ("walk", {"distance_m": 1}) if prompt in ("walk", "retry") else ("exec_command", {"cmd": "do not execute"})
                self.pending_tool = {"name": name, "arguments": arguments}
                self.events.put({"id": f"call-{self.turn}", "method": "item/tool/call", "params": {
                    "threadId": str(self.thread), "turnId": str(self.turn), "callId": f"call-{self.turn}",
                    "tool": name, "arguments": arguments}})
            return {"turn": {"id": str(self.turn)}}
        if method == "turn/interrupt":
            self.notify("turn/completed", {"turn": {"id": str(self.turn), "status": "interrupted", "error": None}})
        return {}

    def notify(self, method, params):
        self.events.put({"method": method, "params": {"threadId": str(self.thread), "turnId": str(self.turn), **params}})

    def send(self, event):
        if "id" not in event or "result" not in event:
            return
        self.last_tool_result = event["result"]
        self.complete_message("In the simulation. Done.", ["In the simulation. ", "Done."])

    def complete_message(self, text, chunks):
        for delta in chunks:
            self.notify("item/agentMessage/delta", {"itemId": "answer", "delta": delta})
        self.notify("item/completed", {"item": {"type": "agentMessage", "id": "answer", "text": text, "phase": "final_answer"}})
        self.notify("thread/tokenUsage/updated", {"tokenUsage": {"total": {
            "inputTokens": 100 * self.turn, "outputTokens": 20 * self.turn,
            "cachedInputTokens": 50 * self.turn}}})
        self.notify("turn/completed", {"turn": {"id": str(self.turn), "status": "completed", "error": None}})

    def close(self):
        self.closed = True


class CodexContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mist-codex-test-")
        self.run_dir = Path(self.temp.name)
        config_dir = self.run_dir / "codex-home"
        config_dir.mkdir()
        (config_dir / "models_cache.json").write_text(json.dumps({"models": [{"slug": "gpt-6-astra"}]}))
        self.env_patch = patch.dict(adapter.os.environ, {"CODEX_HOME": str(config_dir)})
        self.env_patch.start()
        self.command_patch = patch.object(adapter, "codex_command", return_value=[sys.executable])
        self.command_patch.start()
        self.version_patch = patch.object(adapter.subprocess, "run", return_value=SimpleNamespace(stdout="codex-cli 0.153.4", returncode=0))
        self.version_patch.start()
        self.server = FixtureServer()
        real_process = adapter.JsonlProcess

        def process(command, cwd, env=None):
            return real_process(command, cwd, env) if command[0] == "node" else self.server

        self.patch = patch.object(adapter, "JsonlProcess", side_effect=process)
        self.patch.start()
        self.client = adapter.CodexClient(run_dir=self.run_dir, with_memory=False,
                                          system_prompt="Exercise the robot simulator tools and report their receipts.")

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.command_patch.stop()
        self.version_patch.stop()
        self.env_patch.stop()
        self.temp.cleanup()

    def test_real_robot_receipts_stream_and_usage_survive_two_warm_turns(self):
        self.client.set_sim({"walk_scale": 0.97})
        events = []
        for _ in range(2):
            result = self.client.ask("walk", timeout=2, on_event=events.append)
            self.assertEqual(result.errors, [])
            self.assertEqual(result.text, "In the simulation. Done.")
            self.assertEqual(result.input_tokens, 100)
            self.assertEqual(result.output_tokens, 20)
            self.assertEqual(result.cache_read_tokens, 50)
            self.assertEqual(result.tool_calls[0].name, "walk")
            self.assertIsNotNone(result.ttf_tool_s)
            self.assertIsNotNone(result.ttft_s)
        state = json.loads((self.run_dir / "robot_state.json").read_text())
        self.assertEqual(state["x_m"], 1.94)
        receipts = [event for event in events if event["type"] == "tool_execution_end"]
        self.assertIn("SIMULATION", receipts[0]["result"]["content"][0]["text"])
        self.client.new_session()
        self.assertEqual(self.client.ask("walk", timeout=2).input_tokens, 100)

    def test_native_web_search_receipt_is_counted_without_application_tool_grant(self):
        result=self.client.ask('web',timeout=2)
        self.assertEqual(result.errors,[])
        self.assertEqual(result.timings['native_web_search_calls'],1)
        self.assertEqual(len(result.timings['web_search_durations_s']),1)
        self.assertEqual(result.tool_calls,[])
        self.assertIsNotNone(result.ttf_tool_s)

    def test_thermal_interlock_is_preserved(self):
        self.client.set_sim({"servo_temp_max_c": 50})
        result = self.client.ask("walk", timeout=2)
        self.assertTrue(result.tool_calls[0].is_error)
        self.assertFalse(self.server.last_tool_result["success"])
        self.assertIn("cutoff 50C", self.server.last_tool_result["contentItems"][0]["text"])
        self.assertEqual(json.loads((self.run_dir / "robot_state.json").read_text())["x_m"], 0)

    def test_unknown_tool_cannot_execute_and_never_reaches_robot_bus(self):
        result = self.client.ask("unknown", timeout=2)
        self.assertTrue(result.tool_calls[0].is_error)
        self.assertFalse((self.run_dir / "bus.jsonl").exists())

    def test_timeout_requires_fresh_client_and_does_not_leak_late_events(self):
        result = self.client.ask("timeout", timeout=0.03)
        self.assertIn("timeout", result.errors[0])
        self.assertTrue(self.server.closed)
        self.assertIsNotNone(self.client._bridge.proc.poll())
        with self.assertRaisesRegex(RuntimeError, "cannot be reused"):
            self.client.ask("walk", timeout=2)

    def test_explicit_cancel_finishes_correlated_turn_and_allows_reuse(self):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.client.ask("cancel", timeout=2)))
        thread.start()
        deadline = time.monotonic() + 1
        while self.client._active_turn is None and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.client.cancel_current_turn())
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].errors, ["turn interrupted"])
        self.assertEqual(self.client.ask("walk", timeout=2).errors, [])

    def test_unacknowledged_start_closes_process_without_a_turn_id(self):
        result = self.client.ask("start_timeout", timeout=0.1)
        self.assertIn("acknowledgement timed out", result.errors[0])
        self.assertTrue(self.server.closed)
        self.assertIsNotNone(self.client._bridge.proc.poll())

    def test_recovered_retry_notification_is_not_a_failed_turn(self):
        result = self.client.ask("retry", timeout=2)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.text, "In the simulation. Done.")

    def test_all_thread_and_turn_requests_remove_host_environment(self):
        self.client.ask("walk", timeout=2)
        for method, params in self.server.requests:
            if method in ("thread/start", "turn/start"):
                self.assertEqual(params["environments"], [])
        start = next(params for method, params in self.server.requests if method == "thread/start")
        self.assertEqual(start["approvalPolicy"], "never")
        self.assertEqual(start["sandbox"], "read-only")
        self.assertFalse(start["allowProviderModelFallback"])

    def test_bridge_validates_arguments_before_executing(self):
        receipt = self.client._bridge.request("call", {
            "name": "walk", "arguments": {"distance_m": "one"}}, flat=True)
        self.assertTrue(receipt["isError"])
        self.assertFalse((self.run_dir / "bus.jsonl").exists())

    def test_expression_metadata_streams_without_tool_round_trip_or_spoken_marker(self):
        events = []
        result = self.client.ask("chat_metadata", timeout=2, on_event=events.append)
        self.assertEqual(result.text, "Hello, Moon.")
        self.assertEqual(result.tool_calls, [])
        self.assertEqual([event["expression"] for event in events if event["type"] == "face_expression"], ["happy"])
        speech = "".join(event["assistantMessageEvent"]["delta"] for event in events if event["type"] == "message_update")
        self.assertEqual(speech, "Hello, Moon.")
        self.assertFalse((self.run_dir / "bus.jsonl").exists())
        self.assertFalse((self.run_dir / "robot_state.json").exists())

    def test_context_is_refreshed_without_model_tool_calls_and_records_stage_times(self):
        result = self.client.ask("chat_metadata", timeout=2)
        params = next(params for method, params in self.server.requests if method == "turn/start")
        context = params["additionalContext"]["mist_runtime_capabilities"]
        self.assertEqual(context["kind"], "application")
        snapshot = json.loads(context["value"])
        self.assertFalse(snapshot["motion"]["hardware_connected"])
        self.assertTrue(snapshot["perception"]["requires_fresh_observe"])
        for key in ("client_lock_wait_s", "capability_snapshot_s", "turn_start_ack_s"):
            self.assertGreaterEqual(result.timings[key], 0)
        self.assertEqual(result.timings["tool_calls"], 0)


if __name__ == "__main__":
    unittest.main()
