"""Chrome Hearts polling with a persisted heartbeat for independent health checks."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

CENTRAL = ZoneInfo("America/Chicago")
HEALTH = Path("health.json")
HEARTBEAT_INTERVAL = 300

def seconds_left(now):
    local = now.astimezone(CENTRAL)
    if not 10 <= local.hour < 22:
        return 0
    end = local.replace(hour=22, minute=0, second=0, microsecond=0)
    return (end - local).total_seconds()

def save_state():
    subprocess.run(["git", "add", "state.json", "health.json"], check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 1:
        subprocess.run(["git", "commit", "-m", "Update monitor state and heartbeat"], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    elif diff.returncode != 0:
        raise RuntimeError("Unable to inspect baseline changes")

def main():
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL secret")
    deadline = time.monotonic() + 340 * 60
    failures = 0
    last_heartbeat = 0
    session_id = os.environ.get("GITHUB_RUN_ID", "manual")
    while True:
        remaining = min(seconds_left(datetime.now(CENTRAL)), deadline - time.monotonic())
        if remaining < 1:
            print("Session ended: outside daytime window or session limit.", flush=True)
            return
        started = time.monotonic()
        print("Starting check", datetime.now(CENTRAL).isoformat(), flush=True)
        try:
            result = subprocess.run(["python3", "-u", "monitor.py"], timeout=min(180, remaining))
            if result.returncode != 0:
                raise RuntimeError("Monitor check failed")
            failures = 0
            if started - last_heartbeat >= HEARTBEAT_INTERVAL or not HEALTH.exists():
                HEALTH.write_text(json.dumps({
                    "last_success_utc": datetime.now(timezone.utc).isoformat(),
                    "run_id": session_id,
                    "status": "ok"
                }, indent=2) + "\n")
                last_heartbeat = started
            save_state()
        except (subprocess.TimeoutExpired, RuntimeError, subprocess.CalledProcessError) as exc:
            failures += 1
            print(f"Check failed ({failures}/3): {type(exc).__name__}: {exc}", flush=True)
            if failures >= 3:
                raise RuntimeError("Three consecutive checks failed; inspect run logs")
        delay = min(max(0, 60 - (time.monotonic() - started)),
                    seconds_left(datetime.now(CENTRAL)), deadline - time.monotonic())
        if delay > 0:
            time.sleep(delay)

if __name__ == "__main__":
    main()
