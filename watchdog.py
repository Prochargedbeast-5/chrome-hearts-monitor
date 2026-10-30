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
    if age <= 900:
        print("Healthy")
        return
    # Do not dispatch if a monitoring run is currently in progress or queued.
    runs = request(API + "/actions/workflows/monitor.yml/runs?per_page=30")
    active = [r for r in runs.get("workflow_runs", []) if r.get("status") in ("in_progress", "queued", "waiting", "requested", "pending")]
    if active:
        print("Heartbeat stale, but monitor run is active/queued:", [(r["id"], r["status"]) for r in active])
        return
    print("Stale heartbeat; dispatching recovery")
    request(API + "/actions/workflows/monitor.yml/dispatches", "POST", {"ref":"main"})
    # Alert only on actual recovery action, not routine healthy checks.
    alert("Chrome Hearts monitor: no successful heartbeat for over 15 minutes. Automatic GitHub restart requested. Verify recovery in Actions.")

if __name__ == "__main__":
    main()
