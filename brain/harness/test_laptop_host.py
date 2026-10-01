"""Focused checks for laptop hosting without real tunnels or GitHub writes."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import laptop_host as host


class LaptopHostTests(unittest.TestCase):
    def test_public_failures_keep_same_children_until_operator_stop(self):
        app = unittest.mock.Mock()
        tunnel = unittest.mock.Mock()
        app.poll.return_value = tunnel.poll.return_value = None
        records = []
        probes = []
        def probe(url, **kwargs):
            probes.append((url, kwargs))
            return url.startswith("http://127.0.0.1")
        reason = host.monitor_cycle(
            "https://test.trycloudflare.com", app, tunnel,
            lambda: len(records) == 6, lambda *values: records.append(values),
            probe=probe, sleep=lambda _: None)
        self.assertEqual(reason, "operator_stop")
        self.assertEqual(records[-1], (True, False, 0, 6))
        self.assertEqual(probes[0], ("http://127.0.0.1:9067/trial/session",
                                    {"host": "test.trycloudflare.com"}))
        app.terminate.assert_not_called()
        tunnel.terminate.assert_not_called()

    def test_local_failures_recover_even_when_public_probe_succeeds(self):
        child = unittest.mock.Mock()
        child.poll.return_value = None
        records = []
        reason = host.monitor_cycle(
            "https://test.trycloudflare.com", child, child, lambda: False,
            lambda *values: records.append(values),
            probe=lambda url, **_: url.startswith("https:"), sleep=lambda _: None)
        self.assertEqual(reason, "local_health_failed")
        self.assertEqual(records[-1], (False, True, 3, 0))

    def test_local_recovery_resets_failure_count_and_public_status(self):
        child = unittest.mock.Mock()
        child.poll.return_value = None
        records = []
        def probe(url, **kwargs):
            return len(records) == 2
        reason = host.monitor_cycle(
            "https://test.trycloudflare.com", child, child,
            lambda: len(records) == 3, lambda *values: records.append(values),
            probe=probe, sleep=lambda _: None)
        self.assertEqual(reason, "operator_stop")
        self.assertEqual(records[-1], (True, True, 0, 0))

    def test_child_exit_after_sleep_is_reported_before_probing(self):
        app = unittest.mock.Mock()
        tunnel = unittest.mock.Mock()
        app.poll.side_effect = [None, 7]
        tunnel.poll.return_value = None
        probe = unittest.mock.Mock()
        self.assertEqual(host.monitor_cycle(
            "https://test.trycloudflare.com", app, tunnel, lambda: False,
            unittest.mock.Mock(), probe=probe, sleep=lambda _: None), "app_exit:7")
        probe.assert_not_called()

    def test_previous_logs_are_bounded_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cloudflared.log"
            path.write_bytes(b"old-prefix-last")
            host.preserve_cycle_logs((path,), limit=4)
            previous = Path(tmp) / "cloudflared.previous.log"
            self.assertEqual(previous.read_bytes(), b"last")
            path.write_bytes(b"next")
            host.preserve_cycle_logs((path,), limit=4)
            self.assertEqual(previous.read_bytes(), b"next")

    def test_local_probe_sends_public_host_header(self):
        reply = unittest.mock.MagicMock()
        reply.__enter__.return_value.status = 200
        reply.__enter__.return_value.read.return_value = b'{"remote_mode":true,"authenticated":false}'
        with patch.object(host, "urlopen", return_value=reply) as opening:
            self.assertTrue(host.healthy("http://127.0.0.1:9067/trial/session",
                                         host="test.trycloudflare.com"))
        self.assertEqual(opening.call_args.args[0].get_header("Host"),
                         "test.trycloudflare.com")
        reply.__enter__.return_value.read.assert_called_once_with(4096)

    def test_session_probe_requires_valid_remote_session_body(self):
        bodies = [b'<html>Cloudflare error</html>', b'{', b'[]',
                  b'{"remote_mode":false,"authenticated":false}',
                  b'{"remote_mode":true,"authenticated":"false"}',
                  b'{"remote_mode":true}', b'\xff']
        for body in bodies:
            with self.subTest(body=body):
                reply = unittest.mock.MagicMock()
                reply.__enter__.return_value.status = 200
                reply.__enter__.return_value.read.return_value = body
                with patch.object(host, "urlopen", return_value=reply):
                    self.assertFalse(host.healthy("https://test.trycloudflare.com/trial/session"))
        for authenticated in (True, False):
            reply.__enter__.return_value.read.return_value = json.dumps({
                "remote_mode": True, "authenticated": authenticated}).encode()
            with patch.object(host, "urlopen", return_value=reply):
                self.assertTrue(host.healthy("https://test.trycloudflare.com/trial/session"))

    def test_session_body_timeout_is_unhealthy_and_try_readiness_stays_compatible(self):
        reply = unittest.mock.MagicMock()
        reply.__enter__.return_value.status = 200
        reply.__enter__.return_value.read.side_effect = TimeoutError("body stalled")
        with patch.object(host, "urlopen", return_value=reply):
            self.assertFalse(host.healthy("https://test.trycloudflare.com/trial/session"))
            self.assertTrue(host.healthy("https://test.trycloudflare.com/try"))
        self.assertEqual(reply.__enter__.return_value.read.call_count, 1)

    def test_tunnel_command_uses_selected_protocol(self):
        command = host.tunnel_command(Path("cloudflared.exe"), Path("empty.yaml"), "quic")
        self.assertEqual(command[-2:], ["--protocol", "quic"])
        self.assertIn("http://127.0.0.1:9067", command)
        with self.assertRaises(ValueError):
            host.tunnel_command(Path("cloudflared.exe"), Path("empty.yaml"), "invalid")

    def test_endpoint_never_contains_pair_code_and_offline_clears_url(self):
        online = host.endpoint_payload("online", "https://test.trycloudflare.com", "2026-09-29T10:00:00Z")
        offline = host.endpoint_payload("offline", None, online["last_seen_at"])
        self.assertEqual(online["api_origin"], "https://test.trycloudflare.com")
        self.assertIsNone(offline["api_origin"])
        self.assertEqual(offline["last_seen_at"], online["last_seen_at"])
        self.assertNotIn("pair", json.dumps(online))

    def test_gh_publish_uses_stdin_and_fixed_repository(self):
        payload = host.endpoint_payload("online", "https://test.trycloudflare.com", None)
        calls = []
        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if "--jq" in args:
                return subprocess.CompletedProcess(args, 0, "a" * 40, "")
            return subprocess.CompletedProcess(args, 0, "{}", "")
        with patch.object(host.subprocess, "run", side_effect=fake_run):
            host.publish_endpoint(payload)
        self.assertEqual(calls[0][0][2], "repos/silmoon04/mist/contents/docs/endpoint.json")
        self.assertEqual(calls[1][0][1:4], ["api", "--method", "PUT"])
        self.assertNotIn("test.trycloudflare.com", " ".join(calls[1][0]))
        body = json.loads(calls[1][1]["input"])
        self.assertEqual(body["sha"], "a" * 40)
        self.assertNotIn("pair", json.dumps(body))

    def test_publish_retries_once_and_surfaces_failure(self):
        with patch.object(host.subprocess, "run", side_effect=OSError("offline")) as run:
            with self.assertRaises(OSError):
                host.publish_endpoint(host.endpoint_payload("offline", None, None), sleep=lambda _: None)
        self.assertEqual(run.call_count, 2)

    def test_single_instance_lock_and_stop_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with host.InstanceLock(directory / "supervisor.lock"):
                with self.assertRaises(RuntimeError):
                    with host.InstanceLock(directory / "supervisor.lock"):
                        pass
            h = host.Host(directory, Path("cloudflared"), Path(sys.executable), "gh")
            h.state("online", "https://test.trycloudflare.com")
            self.assertTrue(host.request_stop(directory))
            self.assertTrue(h.stop_requested())
            host.atomic_json(directory / "stop.json", {"instance": "stale"})
            self.assertFalse(h.stop_requested())

    def test_url_parser_rejects_other_domains_and_terminate_only_child(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "tunnel.log"
            log.write_text("https://evil.example.com\nhttps://Safe-123.trycloudflare.com\n")
            class Alive:
                def poll(self): return None
            self.assertEqual(host.await_url(log, Alive(), lambda: False, timeout=1),
                             "https://safe-123.trycloudflare.com")
            child = unittest.mock.Mock()
            child.poll.return_value = None
            host.terminate(child)
            child.terminate.assert_called_once()
            child.wait.assert_called_once()

    def test_occupied_origin_port_fails_closed_without_touching_listener(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        try:
            port = listener.getsockname()[1]
            with self.assertRaises(OSError):
                host.reserve_port(port)
            # The supervisor's failed reservation has no handle to the owner.
            self.assertEqual(listener.getsockname()[1], port)
            self.assertTrue(listener.fileno() >= 0)
        finally:
            listener.close()

    def test_readiness_keeps_same_tunnel_through_dns_propagation(self):
        elapsed = [0]
        calls = []
        class Alive:
            def poll(self): return None
        def probe(url):
            calls.append(url)
            return elapsed[0] >= 85
        def sleep(seconds):
            elapsed[0] += seconds
        origin = "https://fresh.trycloudflare.com"
        self.assertTrue(host.await_readiness(
            origin, Alive(), Alive(), lambda: False,
            probe=probe, clock=lambda: elapsed[0], sleep=sleep,
        ))
        self.assertEqual(elapsed[0], 85)
        self.assertEqual(set(calls), {origin + "/try"})


if __name__ == "__main__":
    unittest.main()
