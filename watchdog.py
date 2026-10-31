"""Independent monitor health watchdog. GitHub scheduling is best-effort."""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
API = "https://api.github.com/repos/" + REPO

def request(url, method="GET", data=None):
    req = Request(url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
        method=method)
    with urlopen(req, timeout=25) as resp:
        return json.loads(resp.read() or b"{}") if resp.status != 204 else {}

def alert(message):
    if not WEBHOOK:
        raise RuntimeError("No Discord webhook configured; cannot alert")
    # Use the same request headers and delivery checks as product alerts.
    from monitor import notify
    notify(WEBHOOK, message)

def main():
    now = datetime.now(timezone.utc)
    local = now.astimezone(__import__("zoneinfo").ZoneInfo("America/Chicago"))
    if not 10 <= local.hour < 22:
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
    if age <= 900 and active and not refresh:
        print("Healthy")
        return
    stale = []
    for run in active:
        # run_started_at includes time queued; use actual job start for grace.
        jobs = request(API + f"/actions/runs/{run['id']}/jobs").get("jobs", [])
        running = [j for j in jobs if j.get("status") == "in_progress" and j.get("started_at")]
        if not running:
            continue
        youngest = min((now - datetime.fromisoformat(j["started_at"].replace("Z", "+00:00"))).total_seconds() for j in running)
        if youngest < 900 and not refresh:
            print("New monitor session has startup grace")
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
    alert("Chrome Hearts monitor: automatic restart requested. Saved product alerts will retry. Recovery will be checked by the watchdog.")

if __name__ == "__main__":
    main()

