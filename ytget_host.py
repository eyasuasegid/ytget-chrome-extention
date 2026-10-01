#!/usr/bin/env python3
"""ytget Chrome Native Messaging Host
Allows the ytget Chrome extension to start and stop the local companion server with one click.
"""

from __future__ import annotations

import json
import os
import signal
import struct
import subprocess
import sys
import time
import tempfile
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PID_FILE = Path(tempfile.gettempdir()) / "ytget_server.pid"
LOG_FILE = PROJECT_DIR / "ytget_server.log"
SERVER_URL = "http://127.0.0.1:8765/status"


def is_server_running() -> bool:
    try:
        req = urllib.request.Request(SERVER_URL, method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_python_executable() -> str:
    candidates = [
        PROJECT_DIR / "venv" / "Scripts" / "python.exe",
        PROJECT_DIR / ".venv" / "Scripts" / "python.exe",
        PROJECT_DIR / "venv" / "bin" / "python",
        PROJECT_DIR / ".venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists() and (sys.platform == "win32" or os.access(p, os.X_OK)):
            return str(p)
    return sys.executable


def start_server() -> dict:
    if is_server_running():
        return {"status": "already_running", "message": "Server is already running on port 8765"}

    py_exe = get_python_executable()
    server_script = PROJECT_DIR / "ytget_server.py"

    if not server_script.exists():
        return {"status": "error", "message": f"Server script not found: {server_script}"}

    try:
        # Launch detached background process
        with open(LOG_FILE, "a") as log:
            popen_kwargs = {
                "cwd": str(PROJECT_DIR),
                "stdout": log,
                "stderr": log,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform == "win32":
                # DETACHED_PROCESS = 0x00000008, CREATE_NEW_PROCESS_GROUP = 0x00000200
                popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
            else:
                popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen([py_exe, str(server_script)], **popen_kwargs)

        PID_FILE.write_text(str(proc.pid))

        # Wait up to 3 seconds for server to come online
        for _ in range(15):
            time.sleep(0.2)
            if is_server_running():
                return {
                    "status": "started",
                    "message": "ytget server started successfully",
                    "pid": proc.pid,
                }

        # If not responding yet, check if process died
        if proc.poll() is not None:
            return {
                "status": "error",
                "message": f"Server process exited immediately with code {proc.returncode}. Check {LOG_FILE.name}.",
            }

        return {"status": "started", "message": "Server process launched", "pid": proc.pid}

    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def stop_server() -> dict:
    stopped_any = False

    # 1. Try PID file
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=3,
                )
            else:
                os.kill(pid, signal.SIGTERM)
            stopped_any = True
        except Exception:
            pass
        finally:
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    # 2. Terminate any leftover processes on POSIX
    if sys.platform != "win32":
        try:
            res = subprocess.run(
                ["pkill", "-f", "ytget_server.py"],
                capture_output=True,
                timeout=2,
            )
            if res.returncode == 0:
                stopped_any = True
        except Exception:
            pass

        time.sleep(0.3)
        if is_server_running():
            subprocess.run(["pkill", "-9", "-f", "ytget_server.py"], capture_output=True)
            time.sleep(0.3)

    return {"status": "stopped", "message": "ytget server stopped successfully"}


def read_message() -> dict | None:
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length or len(raw_length) < 4:
        return None
    message_length = struct.unpack("=I", raw_length)[0]
    if message_length <= 0 or message_length > 10 * 1024 * 1024:
        return None
    message_bytes = sys.stdin.buffer.read(message_length)
    return json.loads(message_bytes.decode("utf-8"))


def send_message(message: dict) -> None:
    encoded = json.dumps(message).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main():
    # If run from command line with CLI commands (start, stop, status)
    if len(sys.argv) > 1 and not sys.argv[1].startswith("chrome-extension://"):
        cmd = sys.argv[1].lower()
        if cmd == "start":
            print(json.dumps(start_server(), indent=2))
        elif cmd == "stop":
            print(json.dumps(stop_server(), indent=2))
        elif cmd in ("status", "check"):
            print(json.dumps({"running": is_server_running()}, indent=2))
        else:
            print(f"Usage: {sys.argv[0]} [start|stop|status]")
        return

    # Chrome Native Messaging loop
    while True:
        try:
            msg = read_message()
            if msg is None:
                break
            action = msg.get("action", "").lower()
            if action == "start":
                resp = start_server()
            elif action == "stop":
                resp = stop_server()
            elif action == "status":
                resp = {"status": "online" if is_server_running() else "offline", "running": is_server_running()}
            else:
                resp = {"status": "error", "message": f"Unknown action: {action}"}
            send_message(resp)
        except (KeyboardInterrupt, BrokenPipeError, EOFError):
            break
        except Exception as exc:
            send_message({"status": "error", "message": str(exc)})


if __name__ == "__main__":
    main()
