"""Continuous read-only Tapo collection; no Telegram or relay actions."""
import asyncio
import fcntl
import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime, timezone

from read_tapo import read_once, atomic_json, BASE
from voltage_data import connect_writer, record, INTERVAL


def main():
    import os
    os.umask(0o077)
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    logger = logging.getLogger("tapo-monitor")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(BASE / "monitor.log", maxBytes=262144, backupCount=2)
    logger.addHandler(handler)
    with (BASE / ".monitor.lock").open("a") as singleton:
        try:
            fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Monitor already running.")
        db = connect_writer()
        while True:
            began = time.monotonic()
            try:
                # Reuse the reader lock: manual reads and collection never overlap.
                with (BASE / ".reader.lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    path = BASE / "credentials.json"
                    if path.stat().st_mode & 0o077:
                        raise PermissionError()
                    credentials = json.loads(path.read_text())
                    report = asyncio.run(read_once(credentials["host"], credentials))
                    stamp = time.time()
                    report["checked_at"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                    atomic_json(BASE / "latest.json", report)
                    if report.get("reachable"):
                        atomic_json(BASE / "last_success.json", report)
                    record(db, report, stamp)
                    atomic_json(BASE / "monitor-health.json",
                                {"checked_at": stamp, "collector_ok": True,
                                 "plug_reachable": report.get("reachable") is True,
                                 "complete": report.get("complete") is True})
            except BlockingIOError:
                pass
            except Exception as exc:
                # Never log exception strings that could contain credentials.
                logger.error("%s %s", time.strftime("%Y-%m-%dT%H:%M:%S"), type(exc).__name__)
                atomic_json(BASE / "monitor-health.json",
                            {"checked_at": time.time(), "collector_ok": False, "error": type(exc).__name__})
            time.sleep(max(1, INTERVAL - (time.monotonic() - began)))


if __name__ == "__main__":
    main()
