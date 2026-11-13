"""Independent monitor health watchdog. GitHub scheduling is best-effort."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
API = "https://api.github.com/repos/" + REPO

def request(url, method="GET", data=None):
    req = Request(url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
        method=method)
    # Retry read-only status checks, but never blindly repeat a restart/cancel.
    for attempt in range(4):
        try:
            with urlopen(req, timeout=25) as resp:
                return json.loads(resp.read() or b"{}") if resp.status != 204 else {}
        except (HTTPError, URLError, TimeoutError) as exc:
            transient = not isinstance(exc, HTTPError) or exc.code in (429, 500, 502, 503, 504)
            if method != "GET" or not transient or attempt == 3:
                raise
            delay = 2 ** (attempt + 1)
            if isinstance(exc, HTTPError) and exc.code == 429:
                try:
                    delay = max(delay, float(exc.headers.get("Retry-After", 0)))
                except (ValueError, TypeError, AttributeError):
                    pass
            print(f"Temporary GitHub status error; retry {attempt + 1}/3", flush=True)
            time.sleep(min(delay, 30))

def alert(message):
    if not WEBHOOK:
        raise RuntimeError("No Discord webhook configured; cannot alert")
    # Use the same request headers and delivery checks as product alerts.
    from monitor import notify
    notify(WEBHOOK, message)

def main():
    now = datetime.now(timezone.utc)
    local = now.astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago"))
    if not 570 <= local.hour * 60 + local.minute < 1320:
        print("Outside monitoring hours")
        return
    # Always fetch the latest committed heartbeat, not the watchdog checkout.
    try:
        import base64
        data = request(API + "/contents/health.json?ref=main")
        health = json.loads(base64.b64decode(data["content"]))
        last = datetime.fromisoformat(health["last_success_utc"])
        age = (now - last).total_seconds()
    except Exception as exc:
        age = float("inf")
        print("No readable heartbeat:", exc)
    print("Heartbeat age (seconds):", age)
    runs = request(API + "/actions/workflows/monitor.yml/runs?per_page=50")
    active = [r for r in runs.get("workflow_runs", []) if r.get("status") in
              ("in_progress", "queued", "waiting", "requested", "pending")]
    refresh = os.environ.get("REFRESH_MONITOR") == "true"
    if age <= 900 and not refresh:
        # Monitor sessions intentionally finish after four minutes. A fresh
        # successful heartbeat is healthy even when no Actions job is active.
        print("Healthy: recent successful check; no recovery required")
        return
    stale = []
    for run in active:
        # run_started_at includes time queued; use actual job start for grace.
        jobs = request(API + f"/actions/runs/{run['id']}/jobs").get("jobs", [])
        running = [j for j in jobs if j.get("status") == "in_progress" and j.get("started_at")]
        if not running:
            continue
        youngest = min((now - datetime.fromisoformat(j["started_at"].replace("Z", "+00:00"))).total_seconds() for j in running)
        # A stale success heartbeat does not prove a live job is hung. Allow
        # extra recovery time for storefront failures and delayed state commits.
        # Only recycle a running job after 30 minutes without a successful check.
        if not refresh and (youngest < 1800 or age <= 1800):
            print("Monitor still running; extended recovery grace",
                  "job_age_seconds=", youngest, "heartbeat_age_seconds=", age)
            return
        stale.append(run)
    for run in stale:
        print("Cancelling stale/old monitor session", run["id"])
        request(API + f"/actions/runs/{run['id']}/cancel", "POST")
    if active and not stale:
        print("Replacement monitor already queued; waiting for GitHub runner")
        return
    print("Dispatching fresh monitor session")
    request(API + "/actions/workflows/monitor.yml/dispatches", "POST", {"ref": "main"})
    alert(f"Chrome Hearts monitor: recovery restart requested (last successful check {int(age / 60) if age != float('inf') else 'unknown'} minutes ago). Saved product alerts will retry. Recovery will be checked by the watchdog.")

if __name__ == "__main__":
    main()



