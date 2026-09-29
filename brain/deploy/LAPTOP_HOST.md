# Laptop hosting

`laptop_host.py` runs the private voice trial app on `127.0.0.1:9067` behind a
Cloudflare quick tunnel and publishes `docs/endpoint.json` to `silmoon04/mist`.
The existing local service on port 9053 is separate. The laptop must stay awake,
online, and signed in to `gh` for endpoint publication.
The supervisor reserves port 9067 before opening a tunnel. If another listener
already owns it, hosting stays offline and that listener is left alone.
After cloudflared reports a new hostname, the supervisor allows up to five
minutes for DNS propagation and public HTTPS readiness before replacing it.

Run `powershell -File brain/deploy/start_laptop_host.ps1` to start it with no visible
child console. Use `-Action Status` or `-Action Stop` with the same script. For
manual foreground diagnostics, run `python brain/deploy/laptop_host.py run`. The
lock rejects a second supervisor, and `stop` asks the running instance to shut
down through an instance-specific state file. It does not kill a process by PID.
On Windows, the tunnel and app are attached to a process job that closes them if
the supervisor crashes.

When launching from a clean release checkout, pass `-StateDir` with the absolute
path to the private state directory already in use. Pass the same value for
`-Action Status` and `-Action Stop`; for example:

```powershell
powershell -File brain/deploy/start_laptop_host.ps1 -Action Status -StateDir 'C:\private\mist\laptop-hosting'
```

Omitting `-StateDir` keeps the default `brain/results/laptop-hosting/` relative
to the checkout containing the script. A login task must include `-StateDir` if
the private directory is outside that checkout.

Private state lives in `brain/results/laptop-hosting/`, including the pairing
code, logs, trials, supervisor state, and an empty task-specific Cloudflare config.
The supervisor passes `--config` to cloudflared, so the user's existing
`~/.cloudflared/config.yaml` is neither loaded nor changed. Read the pairing code
locally from `brain/results/laptop-hosting/pair-code.txt`; never place it in a URL
or the Pages repository. Treat the trial traces as private conversation records.

The public endpoint contains only its schema version, online/offline status,
current API origin when online, last update time, and last successful remote
probe time. The supervisor updates GitHub on a state or tunnel URL transition,
not on every probe. The page should probe the API before declaring it reachable:
a laptop crash or lost network can leave the last published state stale. If GitHub
is temporarily unavailable, hosting continues and `state.json` records the
publication error; the supervisor retries during later health checks.

For auto start at login, register a Windows Scheduled Task whose action is
`powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File
<absolute-path-to-brain/deploy/start_laptop_host.ps1>` and trigger is user logon.
Registration should happen after a successful interactive smoke test. Use the
same Windows account so `gh` authentication and any provider credentials remain
available to the app.

The focused offline checks are `python brain/harness/test_laptop_host.py`.
