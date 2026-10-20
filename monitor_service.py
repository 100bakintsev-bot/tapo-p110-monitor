"""Runit adapter: terminate the proot process group, including its Python child."""
import os
import signal
import subprocess
import time
import sys
from pathlib import Path

stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    root = Path(__file__).resolve().parent
    prefix = os.environ.get("PREFIX", "")
    proot = Path(prefix)/"bin/proot-distro"
    data = os.environ.get("TAPO_DATA_DIR", str(Path.home()/".local/share/tapo-p110-monitor"))
    zone = os.environ.get("TAPO_TIMEZONE", "Europe/Kyiv")
    command = [sys.executable, "-B", str(root/"monitor.py")]
    if prefix and proot.exists():
        command = [str(proot), "login", "debian", "--", "env",
                   "TAPO_DATA_DIR="+data, "TAPO_TIMEZONE="+zone,
                   str(root/".venv/bin/python"), "-B", str(root/"monitor.py")]
    child = subprocess.Popen(command, start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while child.poll() is None and not stopping:
            time.sleep(0.2)
    finally:
        # Also clean descendants if proot exits before its child.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=6)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    return 0 if stopping else child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
