"""Short-lived, bounded upload parsers over private pipes; never a network service."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 14 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


def _windows_job(process: subprocess.Popen, memory_bytes: int) -> Any:
    """Set limits before sending document bytes; fail closed if unsupported."""
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t), ("active_process_limit", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError("Cannot create document parser memory limit")
    limits = Extended()
    limits.basic.flags = 0x2000 | 0x100 | 0x8  # kill on close, process memory, one process
    limits.basic.active_process_limit = 1
    limits.process_memory = memory_bytes
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
        kernel.CloseHandle(handle)
        raise OSError("Cannot apply document parser memory limit")
    return lambda: kernel.CloseHandle(handle)


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, OSError):
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass


def _resident_reader() -> Any:
    """Darwin lacks a usable hard RSS rlimit; supervise resident bytes instead.

    This sampled limit can overshoot between checks. Windows uses a hard Job
    Object limit, and other POSIX workers use a hard address-space rlimit.
    """
    import ctypes
    import struct
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_ulonglong, ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    def resident(pid: int) -> int:
        buffer = ctypes.create_string_buffer(128)
        count = library.proc_pidinfo(pid, 4, 0, buffer, len(buffer))  # PROC_PIDTASKINFO
        if count < 16:
            raise OSError("Cannot inspect document parser memory")
        return struct.unpack_from("QQ", buffer)[1]
    return resident


class IsolatedParser:
    def __init__(self, *, timeout: float = 15.0, memory_bytes: int = 384 * 1024 * 1024) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.05 <= timeout <= 120:
            raise ValueError("Upload parse timeout must be between 0.05 and 120 seconds")
        if type(memory_bytes) is not int or not 64 * 1024 * 1024 <= memory_bytes <= 2 * 1024 * 1024 * 1024:
            raise ValueError("Upload parser memory limit must be between 64 MiB and 2 GiB")
        self.timeout, self.memory_bytes = timeout, memory_bytes
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()
        self._closed = False

    def prepare(self, name: str, content_base64: str) -> tuple[dict[str, Any], bytes]:
        from .attachments import AttachmentError, _decode
        payload = json.dumps({"name": name, "content_base64": content_base64}, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_INPUT_BYTES:
            raise AttachmentError("Document parser input exceeds 14 MiB")
        process = None
        close_job = None
        readers: list[threading.Thread] = []
        output = bytearray()
        oversized = threading.Event()
        try:
            with self._lock:
                if self._closed:
                    raise AttachmentError("Document parsing is shutting down")
                command = [sys.executable, "-I", str(Path(__file__).resolve()), "--worker", str(self.memory_bytes)]
                dependency = importlib.util.find_spec("pypdf")
                if dependency is not None and dependency.origin:
                    # Preserve only the installed parser's resolved package
                    # directory, not an unrestricted inherited PYTHONPATH.
                    command.append(str(Path(dependency.origin).resolve().parent.parent))
                process = subprocess.Popen(command,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           start_new_session=os.name == "posix")
                self._processes.add(process)
            if os.name == "nt":
                close_job = _windows_job(process, self.memory_bytes)
            resident = _resident_reader() if sys.platform == "darwin" else None
            if resident is not None:
                resident(process.pid)  # establish monitoring before sending input
            def read_output() -> None:
                try:
                    while True:
                        chunk = process.stdout.read(min(65536, MAX_OUTPUT_BYTES + 1 - len(output)))
                        if not chunk:
                            return
                        output.extend(chunk)
                        if len(output) > MAX_OUTPUT_BYTES:
                            oversized.set()
                            _kill(process)
                            return
                except (OSError, ValueError):
                    return
            def write_input() -> None:
                try:
                    process.stdin.write(payload)
                    process.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass
            readers = [threading.Thread(target=read_output, daemon=True), threading.Thread(target=write_input, daemon=True)]
            for thread in readers:
                thread.start()
            deadline = time.monotonic() + self.timeout
            while process.poll() is None:
                if resident is not None:
                    try:
                        memory = resident(process.pid)
                    except OSError:
                        # Darwin can remove the resident-memory sample just before waitpid sees
                        # exit. Permit only a short exit grace period; a worker
                        # that remains alive without monitoring fails closed.
                        try:
                            process.wait(timeout=0.025)
                        except subprocess.TimeoutExpired:
                            raise OSError("Document parser memory monitoring became unavailable") from None
                        else:
                            break
                    if memory > self.memory_bytes:
                        raise AttachmentError("Document parsing exceeded its memory limit. Split the document or upload plain text instead.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AttachmentError("Document parsing exceeded its time limit. Split the document or upload plain text instead.")
                try:
                    process.wait(timeout=min(0.025, remaining))
                except subprocess.TimeoutExpired:
                    pass
            for thread in readers:
                thread.join(timeout=1)
            if oversized.is_set():
                raise AttachmentError("Document parser output exceeded its limit")
            if process.returncode != 0 or not output:
                raise AttachmentError("Document parser stopped or exceeded its memory limit. Split the document or upload plain text instead.")
            response = json.loads(output)
            if isinstance(response, dict) and isinstance(response.get("error"), str):
                raise AttachmentError(response["error"][:1024])
            record = response.get("record") if isinstance(response, dict) else None
            body = _decode(content_base64)
            if not isinstance(record, dict) or record.get("name") != name or record.get("size_bytes") != len(body) or record.get("sha256") != hashlib.sha256(body).hexdigest():
                raise AttachmentError("Document parser returned invalid metadata")
            if record.get("kind") == "text":
                if not isinstance(record.get("text"), str) or not record["text"].strip() or len(record["text"]) > 200000:
                    raise AttachmentError("Document parser returned invalid text")
            elif record.get("kind") == "image":
                if any(type(record.get(key)) is not int or not 0 < record[key] <= 20000 for key in ("width", "height")) or record["width"] * record["height"] > 40000000:
                    raise AttachmentError("Document parser returned invalid image dimensions")
            else:
                raise AttachmentError("Document parser returned invalid attachment kind")
            return record, body
        except AttachmentError:
            raise
        except Exception:
            raise AttachmentError("The isolated document parser could not start or return a valid result. Check the Python environment and upload plain text instead.") from None
        finally:
            if process is not None:
                _kill(process)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                for pipe in (process.stdin, process.stdout):
                    if pipe:
                        pipe.close()
                for thread in readers:
                    thread.join(timeout=1)
                with self._lock:
                    self._processes.discard(process)
            if close_job:
                close_job()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            processes = list(self._processes)
        for process in processes:
            _kill(process)


def _worker(memory_bytes: int, dependency_root: str | None = None) -> None:
    # No preexec_fn in a threaded server: establish POSIX limits here before
    # importing document libraries or reading any uploaded bytes.
    if os.name == "posix":
        import resource
        if sys.platform != "darwin":
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    if dependency_root:
        sys.path.append(dependency_root)
    from orchestrator.chat.attachments import prepare_attachment
    try:
        encoded = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(encoded) > MAX_INPUT_BYTES:
            raise ValueError("Document parser input exceeds 14 MiB")
        request = json.loads(encoded)
        record, _ = prepare_attachment(request["name"], request["content_base64"])
        output = json.dumps({"record": record}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError("Document parser output exceeded its limit")
    except MemoryError:
        output = b'{"error":"Document parsing exceeded its memory limit. Split the document or upload plain text instead."}'
    except ValueError as error:
        output = json.dumps({"error": str(error)[:1024]}).encode("utf-8")
    except Exception:
        output = b'{"error":"The document could not be parsed safely. Upload plain text instead."}'
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4) or sys.argv[1] != "--worker":
        raise SystemExit(2)
    _worker(int(sys.argv[2]), sys.argv[3] if len(sys.argv) == 4 else None)
