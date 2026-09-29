"""Run the private MIST trial server behind a rotating Cloudflare quick tunnel.

The only public write is docs/endpoint.json in silmoon04/mist. Never put the
pairing code in that file, process arguments, or the tunnel URL.
"""

from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid


BRAIN = Path(__file__).resolve().parents[1]
ROOT = BRAIN.parent
DEFAULT_STATE_DIR = BRAIN / "results" / "laptop-hosting"
REPO = "silmoon04/mist"
ENDPOINT = "docs/endpoint.json"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com\b", re.I)
PORT = 9067
PROTOCOLS = ("auto", "quic", "http2")
POLL_SECONDS = 1
CHECK_SECONDS = 15
READINESS_SECONDS = 300
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


class InstanceLock:
    """Advisory lock held by the supervisor; no PID-based process killing."""

    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            raise RuntimeError("MIST laptop host is already running") from exc
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()


def endpoint_payload(status: str, origin: str | None, last_seen: str | None) -> dict:
    return {
        "version": 1,
        "status": status,
        "api_origin": origin if status == "online" else None,
        "updated_at": now(),
        "last_seen_at": last_seen,
    }


def publish_endpoint(payload: dict, *, gh: str = "gh", sleep=time.sleep) -> None:
    """GitHub Contents API: a fixed destination and a JSON body on stdin."""
    route = f"repos/{REPO}/contents/{ENDPOINT}"
    for attempt in range(2):
        try:
            existing = subprocess.run(
                [gh, "api", route, "--jq", ".sha"],
                text=True, capture_output=True, check=False, creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
            if existing.returncode:
                error = (existing.stderr or "").lower()
                if "404" not in error and "not found" not in error:
                    raise RuntimeError(f"GitHub endpoint lookup failed: {existing.stderr.strip()[:240]}")
                sha = None
            else:
                sha = existing.stdout.strip()
                if not re.fullmatch(r"[0-9a-f]{40}", sha):
                    raise RuntimeError("GitHub returned an invalid endpoint SHA")
            # gh api --input - accepts the exact JSON bytes on stdin. No shell is used.
            import base64
            content = base64.b64encode((json.dumps(payload, indent=2) + "\n").encode()).decode()
            body = {"message": f"Update MIST endpoint: {payload['status']}", "content": content, "branch": "main"}
            if sha:
                body["sha"] = sha
            result = subprocess.run(
                [gh, "api", "--method", "PUT", route, "--input", "-"],
                input=json.dumps(body), text=True, capture_output=True, check=False,
                creationflags=CREATE_NO_WINDOW, timeout=20,
            )
            if result.returncode:
                raise RuntimeError(f"GitHub endpoint update failed: {result.stderr.strip()[:240]}")
            return
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            if attempt:
                raise
            sleep(2)


def healthy(url: str, timeout: int = 8) -> bool:
    try:
        with urlopen(Request(url, headers={"User-Agent": "MIST-laptop-host/1"}), timeout=timeout) as reply:
            return reply.status == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def reserve_port(port: int = PORT) -> socket.socket:
    """Hold the origin port closed to other listeners until our app starts."""
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            reservation.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        reservation.bind(("127.0.0.1", port))
        reservation.listen(1)
        return reservation
    except OSError:
        reservation.close()
        raise


def tunnel_command(cloudflared: Path, config: Path, protocol: str) -> list[str]:
    if protocol not in PROTOCOLS:
        raise ValueError(f"Unsupported tunnel protocol: {protocol}")
    return [str(cloudflared), "--config", str(config), "tunnel",
            "--url", f"http://127.0.0.1:{PORT}", "--protocol", protocol]


def await_url(log_path: Path, tunnel: subprocess.Popen, stopped, timeout: int = 90) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and tunnel.poll() is None and not stopped():
        with contextlib.suppress(OSError):
            match = URL_RE.search(log_path.read_text(encoding="utf-8", errors="replace"))
            if match:
                return match.group(0).lower()
        time.sleep(POLL_SECONDS)
    return None


def await_readiness(origin: str, app: subprocess.Popen, tunnel: subprocess.Popen,
                    stopped, *, timeout: int = READINESS_SECONDS,
                    probe=healthy, clock=time.monotonic, sleep=time.sleep) -> bool:
    """Give a new quick-tunnel hostname time to propagate through local DNS."""
    deadline = clock() + timeout
    while clock() < deadline and not stopped():
        if app.poll() is not None or tunnel.poll() is not None:
            return False
        if probe(origin + "/try"):
            return True
        sleep(POLL_SECONDS)
    return False


def terminate(child: subprocess.Popen | None) -> None:
    if child is None or child.poll() is not None:
        return
    try:
        child.terminate()
    except OSError:
        pass
    try:
        child.wait(timeout=12)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)


class OwnedProcessJob:
    """Windows job closes all assigned children if the supervisor crashes."""

    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "Could not create process job")
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            raise OSError(error, "Could not configure process job")
        self.kernel = kernel
        self.handle = handle

    def assign(self, child: subprocess.Popen) -> None:
        if self.handle is None:
            return
        import ctypes
        if not self.kernel.AssignProcessToJobObject(self.handle, child._handle):
            error = ctypes.get_last_error()
            terminate(child)
            raise OSError(error, "Could not attach child to process job")

    def close(self) -> None:
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Host:
    def __init__(self, state_dir: Path, cloudflared: Path, python: Path, gh: str,
                 protocol: str = "auto"):
        self.dir = state_dir.resolve()
        self.cloudflared = cloudflared
        self.python = python
        self.gh = gh
        self.protocol = protocol
        self.identity = uuid.uuid4().hex
        self.state_path = self.dir / "state.json"
        self.stop_path = self.dir / "stop.json"
        self.last_seen = None
        self.published = None

    def log(self, message: str) -> None:
        with (self.dir / "host.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{now()} {message[:300]}\n")

    def state(self, status: str, origin: str | None = None, error: str | None = None) -> None:
        atomic_json(self.state_path, {
            "instance": self.identity, "pid": os.getpid(), "status": status,
            "api_origin": origin, "last_seen_at": self.last_seen,
            "updated_at": now(), "publish_error": error,
        })

    def stop_requested(self) -> bool:
        try:
            return json.loads(self.stop_path.read_text(encoding="utf-8")).get("instance") == self.identity
        except (OSError, ValueError, TypeError):
            return False

    def publish(self, status: str, origin: str | None) -> None:
        payload = endpoint_payload(status, origin, self.last_seen)
        signature = (status, origin)
        if signature == self.published:
            return
        try:
            publish_endpoint(payload, gh=self.gh)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            self.state(status, origin, str(exc)[:300])
            self.log(f"Endpoint publication pending: {exc}")
            print(f"Endpoint publication pending: {exc}", file=sys.stderr, flush=True)
        else:
            self.published = signature
            self.state(status, origin)

    def run(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with InstanceLock(self.dir / "supervisor.lock"):
            job = OwnedProcessJob()
            self.stop_path.unlink(missing_ok=True)
            pair_file = self.dir / "pair-code.txt"
            if not pair_file.exists():
                pair_file.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
                os.chmod(pair_file, 0o600)
            # An explicit empty config prevents loading ~/.cloudflared/config.yaml.
            config = self.dir / "cloudflared-empty.yaml"
            config.write_text("{}\n", encoding="utf-8")
            self.state("starting")
            backoff = 2
            try:
                while not self.stop_requested():
                    tunnel = app = None
                    reservation = None
                    tunnel_log = self.dir / "cloudflared.log"
                    app_log = self.dir / "remote-app.log"
                    origin = None
                    try:
                        reservation = reserve_port()
                        with tunnel_log.open("w", encoding="utf-8") as log:
                            tunnel = subprocess.Popen(
                                tunnel_command(self.cloudflared, config, self.protocol),
                                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW,
                            )
                            job.assign(tunnel)
                        origin = await_url(tunnel_log, tunnel, self.stop_requested)
                        if not origin or self.stop_requested():
                            raise RuntimeError("Tunnel exited or did not provide a public URL")
                        app_env = os.environ.copy()
                        app_env.pop("MIST_REMOTE_PAIR_CODE", None)
                        reservation.close()
                        reservation = None
                        with app_log.open("w", encoding="utf-8") as log:
                            app = subprocess.Popen(
                                [str(self.python), str(BRAIN / "duplex" / "live_studio.py"),
                                 "--port", str(PORT), "--run-dir", str(self.dir / "trials"),
                                 "--remote-origin", origin, "--remote-pair-code-file", str(pair_file)],
                                cwd=ROOT, env=app_env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=CREATE_NO_WINDOW,
                            )
                            job.assign(app)
                        if not await_readiness(origin, app, tunnel, self.stop_requested):
                            raise RuntimeError("Remote app did not become reachable")
                        self.last_seen = now()
                        self.state("online", origin)
                        self.publish("online", origin)
                        backoff = 2
                        if self.stop_requested():
                            continue
                        misses = 0
                        while not self.stop_requested() and app.poll() is None and tunnel.poll() is None:
                            time.sleep(CHECK_SECONDS)
                            if healthy(origin + "/try"):
                                self.last_seen = now()
                                misses = 0
                                if self.published != ("online", origin):
                                    self.publish("online", origin)
                                else:
                                    self.state("online", origin)
                            else:
                                misses += 1
                                if misses >= 3:
                                    break
                    except (OSError, RuntimeError) as exc:
                        self.log(f"Host cycle failed: {exc}")
                        print(f"Host cycle failed: {exc}", file=sys.stderr, flush=True)
                    finally:
                        if reservation is not None:
                            reservation.close()
                        terminate(app)
                        terminate(tunnel)
                    self.state("offline")
                    self.publish("offline", None)
                    if not self.stop_requested():
                        deadline = time.monotonic() + backoff
                        while time.monotonic() < deadline and not self.stop_requested():
                            time.sleep(POLL_SECONDS)
                        backoff = min(backoff * 2, 30)
            finally:
                try:
                    self.state("offline")
                    self.publish("offline", None)
                    self.stop_path.unlink(missing_ok=True)
                finally:
                    job.close()


def request_stop(state_dir: Path) -> bool:
    state_path = state_dir / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        identity = state["instance"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    atomic_json(state_dir / "stop.json", {"instance": identity})
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "stop", "status"))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--cloudflared", type=Path, default=Path.home() / "bin" / "cloudflared.exe")
    parser.add_argument("--protocol", choices=PROTOCOLS, default="auto")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--gh", default="gh")
    args = parser.parse_args()
    if args.command == "stop":
        print("Stop requested" if request_stop(args.state_dir) else "No supervisor state found")
        return 0
    if args.command == "status":
        try:
            print((args.state_dir / "state.json").read_text(encoding="utf-8"))
        except OSError:
            print("No supervisor state found")
        return 0
    try:
        Host(args.state_dir, args.cloudflared, args.python, args.gh, args.protocol).run()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
