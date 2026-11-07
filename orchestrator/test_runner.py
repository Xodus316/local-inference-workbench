"""Run a configured argument vector and retain bounded output and timing."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def run_test(argv, cwd, timeout, cancel):
    allowed = ("PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP", "SystemRoot", "SYSTEMROOT", "WINDIR")
    env = {k: os.environ[k] for k in allowed if k in os.environ}
    flags = {"start_new_session": True} if os.name != "nt" else {}
    command = argv
    if os.name == "nt":
        command = [sys.executable, "-I", str(Path(__file__).with_name("_winjob.py")), *argv]
    started = time.monotonic()
    output = bytearray()
    truncated = False
    reason = None
    with subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, **flags) as proc:
        def drain():
            nonlocal truncated
            while chunk := proc.stdout.read(8192):
                available = max(0, 64000-len(output))
                output.extend(chunk[:available])
                truncated |= len(chunk) > available

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            while proc.poll() is None:
                if cancel.wait(.1) or time.monotonic() - started >= timeout:
                    reason = "cancelled" if cancel.is_set() else "timeout"
                    break
        finally:
            if os.name == "nt":
                if proc.poll() is None:
                    proc.kill()
            else:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=5)
            reader.join(timeout=5)
        return {"argv": argv, "exit_code": proc.returncode, "reason": reason,
                "elapsed_seconds": round(time.monotonic()-started, 3),
                "output": output.decode("utf-8", errors="replace"), "truncated": truncated}
