"""Desktop lifecycle controls and live model activity for a local service."""
from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from .config import Config
from .gpu import read_gpu_stats, _gpu_display_values


def local_json(url: str, limit: int = 2_000_000) -> dict:
    parts = urlsplit(url)
    if (parts.scheme != "http" or not parts.hostname or parts.username or parts.password
            or parts.hostname != "localhost" and not ipaddress.ip_address(parts.hostname).is_loopback):
        raise ValueError("Expected a loopback HTTP address")
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=2)
    try:
        connection.request("GET", parts.path or "/")
        reply = connection.getresponse()
        if reply.status != 200:
            raise OSError(f"Service returned HTTP {reply.status}")
        raw = reply.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Status exceeds display limit")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Invalid status reply")
        return data
    finally:
        connection.close()


def save_config(path: Path, original: bytes | None, text: str) -> Config:
    parsed = Config.model_validate_json(text)
    current = path.read_bytes() if path.exists() else None
    if current != original:
        raise ValueError("The configuration changed on disk. Close and reopen the editor.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(parsed.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return parsed


class LocalProcess:
    def __init__(self, config: Path, data: Path, port: int):
        self.config = config
        self.data = data
        self.port = port
        self.process = None
        self.output = None

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    @property
    def owned(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        if self.owned:
            raise ValueError("This panel already owns a running service")
        if self.output:
            self.output.close()
        if self.config.exists():
            Config.model_validate_json(self.config.read_text(encoding="utf-8-sig"))
        else:
            save_config(self.config, None, Config().model_dump_json())
        self.output = tempfile.TemporaryFile()
        command = [sys.executable, "-m", "orchestrator", "--config", str(self.config),
                   "--data", str(self.data), "--port", str(self.port), "--stop-on-stdin-close"]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=self.output,
                                        stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def request_stop(self, status: dict):
        if not self.owned or self.process.stdin is None or self.process.stdin.closed:
            raise ValueError("This panel does not own a running service")
        if status["inference_active"] or status.get("active_run_id") or status.get("queued", 0):
            raise ValueError("Wait for active work to finish, or cancel it from Workflows or Chat.")
        self.process.stdin.close()

    def diagnostics(self):
        if not self.output or self.owned:
            return ""
        self.output.seek(0, 2)
        self.output.seek(max(0, self.output.tell() - 1400))
        return self.output.read().decode("utf-8", errors="replace").strip()


def main(argv=None):
    import tkinter as tk
    from tkinter import messagebox, ttk

    parser = argparse.ArgumentParser(description=__doc__)
    base = Path.home() / ".local-inference-workbench"
    parser.add_argument("--config", type=Path, default=base / "config.json")
    parser.add_argument("--data", type=Path, default=base)
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    service = LocalProcess(args.config.resolve(), args.data.resolve(), args.port)
    root = tk.Tk()
    root.title("Local Inference Workbench · PC controls")
    root.geometry("980x760")
    root.minsize(690, 470)
    layout = ttk.Frame(root, padding=14)
    layout.pack(fill="both", expand=True)
    status = tk.StringVar(value="Checking service…")
    detail = tk.StringVar(value=service.base)
    ttk.Label(layout, textvariable=status, font=("Segoe UI", 14, "bold")).pack(anchor="w")
    ttk.Label(layout, textvariable=detail).pack(anchor="w", pady=(0, 10))
    toolbar = ttk.Frame(layout)
    toolbar.pack(fill="x")
    messages = queue.Queue()
    state = {"busy": False, "close": False, "stopping": False, "up": False, "last_exit": None}

    def start():
        try:
            service.start()
            state["stopping"] = False
            state["last_exit"] = None
            status.set("Starting service…")
            start_button.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("Start failed", str(exc), parent=root)

    def stop(close=False):
        if state["stopping"]:
            state["close"] = close
            return
        try:
            # Recheck activity at click time instead of relying on the last poll.
            service.request_stop(local_json(service.base + "/api/status"))
            state.update(stopping=True, close=close)
            status.set("Stopping service…")
            stop_button.configure(state="disabled")
        except Exception as exc:
            messagebox.showerror("Stop unavailable", str(exc), parent=root)

    def configure():
        try:
            original = service.config.read_bytes() if service.config.exists() else None
            initial = original.decode("utf-8-sig") if original is not None else Config().model_dump_json(indent=2)
        except (OSError, UnicodeError) as exc:
            messagebox.showerror("Configuration unavailable", str(exc), parent=root)
            return
        dialog = tk.Toplevel(root)
        dialog.title("Service configuration")
        dialog.geometry("680x520")
        ttk.Label(dialog, text=str(service.config), padding=8).pack(anchor="w")
        text = tk.Text(dialog, wrap="none", undo=True)
        text.insert("1.0", initial)
        text.pack(fill="both", expand=True, padx=8)
        ttk.Label(dialog, text="Saved settings take effect at the next service start.", padding=6).pack(anchor="w")
        def save():
            try:
                save_config(service.config, original, text.get("1.0", "end"))
            except Exception as exc:
                messagebox.showerror("Cannot save configuration", str(exc), parent=dialog)
                return
            dialog.destroy()
        ttk.Button(dialog, text="Validate and save", command=save).pack(anchor="e", padx=8, pady=8)
        dialog.transient(root)

    start_button = ttk.Button(toolbar, text="Start", command=start, state="disabled")
    stop_button = ttk.Button(toolbar, text="Stop", command=stop, state="disabled")
    start_button.pack(side="left"); stop_button.pack(side="left", padx=5)
    ttk.Button(toolbar, text="Configuration", command=configure).pack(side="left")
    workflows = ttk.Button(toolbar, text="Open workflows", command=lambda: webbrowser.open(service.base + "/panel"))
    chat = ttk.Button(toolbar, text="Open chat", command=lambda: webbrowser.open(service.base + "/chat/"))
    chat.pack(side="right"); workflows.pack(side="right", padx=5)
    gpu_info = tk.StringVar(value="Checking GPU…")
    ttk.Label(layout, textvariable=gpu_info, wraplength=840, padding=(0,12)).pack(anchor="w")
    def make_table(title, columns):
        section = ttk.LabelFrame(layout, text=title, padding=6)
        section.pack(fill="both", expand=True, pady=4)
        tree = ttk.Treeview(section, columns=tuple(key for key,_ in columns), show="headings", height=4)
        for key,label in columns:
            tree.heading(key, text=label)
            tree.column(key,width=105,stretch=True)
        scroll = ttk.Scrollbar(section, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right",fill="y"); tree.pack(fill="both",expand=True)
        return tree
    models = make_table("Resident models", [("model","Model"),("context","Context"),("memory","GPU / total GiB")])
    runs = make_table("Recent runs", [("id","Run"),("status","Status")])
    calls = make_table("Model calls · active or latest run",
                       [("stage", "Stage"), ("role", "Role"), ("model", "Model"), ("status", "Status")])
    def open_run(event):
        item = runs.identify_row(event.y)
        if item:
            run_id = runs.item(item, "values")[0]
            webbrowser.open(service.base + "/panel#run=" + str(run_id))
    runs.bind("<Double-1>", open_run)

    def poll():
        if not state["busy"]:
            state["busy"] = True
            def read():
                try:
                    current = local_json(service.base + "/api/status")
                except (ValueError, OSError, http.client.HTTPException):
                    current = None
                try:
                    conf = Config.model_validate_json(service.config.read_text(encoding="utf-8-sig")) if service.config.exists() else Config()
                    resident = local_json(conf.ollama_url + "/api/ps").get("models", [])
                except (ValueError,OSError,http.client.HTTPException):
                    resident = []
                rows = (current or {}).get("runs", [])
                run_id = (current or {}).get("active_run_id") or (rows[0]["id"] if rows else None)
                try:
                    activity = local_json(service.base + "/api/runs/" + run_id).get("calls", []) if run_id else []
                except (ValueError, OSError, http.client.HTTPException):
                    activity = []
                messages.put(("data", (current, resident, activity)))
            threading.Thread(target=read, daemon=True).start()
        root.after(2500, poll)
    def gpu_poll():
        def read():
            try:
                metrics = _gpu_display_values(read_gpu_stats())
                summary = "  ·  ".join(metrics[k] for k in ("name","vram","utilization","temperature","power"))
            except (ValueError,OSError,subprocess.TimeoutExpired):
                summary = "GPU telemetry unavailable"
            messages.put(("gpu", summary))
        threading.Thread(target=read, daemon=True).start()
        root.after(30000, gpu_poll)
    def drain():
        while not messages.empty():
            kind, value = messages.get()
            if kind == "gpu":
                gpu_info.set(value)
                continue
            current, resident, activity = value
            state.update(busy=False,up=current is not None)
            up = state["up"]
            owned = service.owned
            if not owned and state["close"]:
                root.destroy()
                return
            status.set("Stopping service…" if owned and state["stopping"] else
                       "Service online" if up else "Starting service…" if owned else "Service offline")
            owner = "Started outside this panel" if up and not owned else service.base
            active = "Inference active" if up and current.get("inference_active") else "Idle"
            detail.set(owner + ("  ·  " + active if up else ""))
            start_button.configure(state="normal" if not up and not owned else "disabled")
            stop_button.configure(state="normal" if up and owned and not state["stopping"] else "disabled")
            for control in (workflows, chat):
                control.configure(state="normal" if up else "disabled")
            models.delete(*models.get_children()); runs.delete(*runs.get_children())
            for row in resident:
                allocated = row.get("size", 0) / 1024**3
                used = row.get("size_vram", 0) / 1024**3
                models.insert("", "end", values=(row.get("name"),row.get("context_length","—"),f"{used:.1f} / {allocated:.1f}"))
            for row in (current or {}).get("runs", [])[:25]:
                runs.insert("", "end", values=(row["id"],row["status"]))
            calls.delete(*calls.get_children())
            for row in activity:
                calls.insert("", "end", values=tuple(row.get(k, "") for k in ("stage", "role", "model", "status")))
            if service.process and not owned and service.process.returncode and state["last_exit"] != service.process.pid:
                state["last_exit"] = service.process.pid
                messagebox.showerror("Service exited", service.diagnostics() or f"Exit code {service.process.returncode}", parent=root)
        root.after(180, drain)
    def on_close():
        if service.owned:
            if messagebox.askokcancel("Close PC controls?", "Stop the service started by this panel and close?", parent=root):
                stop(close=True)
        else:
            root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(50, poll); root.after(50, gpu_poll); root.after(80, drain)
    try:
        root.mainloop()
    finally:
        if service.output:
            service.output.close()


if __name__ == "__main__":
    main()
