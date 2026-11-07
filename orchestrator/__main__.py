"""Launch the local workflow service."""
import argparse
import os
import sys
import threading
from pathlib import Path
from .config import Config
from .runtime import Runtime
from .server import Server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data", type=Path, default=Path.home()/".local-inference-workbench")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--stop-on-stdin-close", action="store_true", help="Stop gracefully when the controlling application closes its pipe")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Choose a valid TCP port")
    os.umask(0o077)
    config = Config.model_validate_json(args.config.read_text(encoding="utf-8-sig")) if args.config else Config()
    runtime = Runtime(args.data, config)
    try:
        with Server(args.port, runtime) as server:
            print(f"Local Inference Workbench: http://127.0.0.1:{args.port}", flush=True)
            if args.stop_on_stdin_close:
                def watch_parent():
                    while sys.stdin.buffer.read(1024):
                        pass
                    server.shutdown()
                threading.Thread(target=watch_parent, daemon=True).start()
            try:
                server.serve_forever(poll_interval=.2)
            except KeyboardInterrupt:
                pass
            finally:
                from .panel import close_ui
                close_ui(server)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
