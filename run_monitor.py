"""Chrome Hearts polling with a persisted heartbeat for independent health checks."""
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from persistence import atomic_write, save_state

CENTRAL = ZoneInfo("America/Chicago")
HEALTH = Path("health.json")
HEARTBEAT_INTERVAL = 300

def seconds_left(now):
    local = now.astimezone(CENTRAL)
    if not 570 <= local.hour * 60 + local.minute < 1320:
        return 0
    end = local.replace(hour=22, minute=0, second=0, microsecond=0)
    return (end - local).total_seconds()

def main():
    if not os.environ.get("DISCORD_WEBHOOK_URL"):
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL secret")
    deadline = time.monotonic() + 340 * 60
    failures = 0
    last_heartbeat = float("-inf")
    session_id = os.environ.get("GITHUB_RUN_ID", "manual")
    last_status = None
    while True:
        remaining = min(seconds_left(datetime.now(CENTRAL)), deadline - time.monotonic())
        if remaining < 1:
            print("Session ended: outside daytime window or session limit.", flush=True)
            return
        started = time.monotonic()
        print("Starting check", datetime.now(CENTRAL).isoformat(), flush=True)
        try:
            result = subprocess.run(["python3", "-u", "monitor.py"], timeout=min(180, remaining))
            if result.returncode == 75:
                print("Storefront unavailable; retrying in one minute.", flush=True)
                if last_status != "unavailable" or started - last_heartbeat >= HEARTBEAT_INTERVAL:
                    health = json.loads(HEALTH.read_text()) if HEALTH.exists() else {}
                    health.update(status="unavailable", run_id=session_id,
                                  last_attempt_utc=datetime.now(timezone.utc).isoformat())
                    atomic_write(HEALTH, health)
                    save_state()
                    last_heartbeat = started
                    last_status = "unavailable"
                failures = 0
            elif result.returncode != 0:
                raise RuntimeError("Monitor check failed")
            else:
                if last_status != "ok" or started - last_heartbeat >= HEARTBEAT_INTERVAL:
                    HEALTH.write_text(json.dumps({
                        "last_success_utc": datetime.now(timezone.utc).isoformat(),
                        "run_id": session_id,
                        "status": "ok"
                    }, indent=2) + "\n")
                    last_heartbeat = started
                save_state()
                last_status = "ok"
                failures = 0
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


