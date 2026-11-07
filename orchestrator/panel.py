"""Local web interfaces sharing one inference service."""
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from urllib.parse import urlsplit

from .chat.service import ChatService
from .chat.store import ChatStore
from .chat.web import ChatWeb

_ASSETS = {
    "/": ("workbench.html", "text/html"),
    "/panel": ("workbench.html", "text/html"),
    "/panel/": ("workbench.html", "text/html"),
    "/panel/style.css": ("workbench.css", "text/css"),
    "/panel/app.js": ("workbench.js", "text/javascript"),
}
_lock = Lock()


def _chat(server):
    with _lock:
        if getattr(server, "chat_closing", False):
            raise RuntimeError("Service is shutting down")
        if getattr(server, "chat_web", None) is None:
            runtime = server.runtime
            backend = SimpleNamespace(ollama=runtime.provider.ollama, inference_lock=runtime.inference_lock)
            store = ChatStore(runtime.store.directory / "chat.sqlite3")
            try:
                service = ChatService(backend, store, default_model=runtime.config.roles.get("fast_primary"),
                                      decision_model=runtime.config.roles.get("decider"))
            except Exception:
                store.close()
                raise
            server.chat_web = ChatWeb(service)
        return server.chat_web


def close_ui(server):
    with _lock:
        server.chat_closing = True
        web = getattr(server, "chat_web", None)
        server.chat_web = None
    if web:
        try:
            web.service.close()
        finally:
            web.service.store.close()


def dispatch_ui(handler, method, path):
    path = urlsplit(path).path
    if method == "GET" and path in _ASSETS:
        name, mime = _ASSETS[path]
        content = (Path(__file__).parent / "static" / name).read_bytes()
        handler.send_response(200)
        handler.send_header("Content-Type", mime + "; charset=utf-8")
        handler.send_header("Content-Length", str(len(content)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        handler.end_headers()
        handler.wfile.write(content)
        return True
    if path == "/chat" or path.startswith("/chat/"):
        if method in ("GET", "POST"):
            web = _chat(handler.server)
            (web.get if method == "GET" else web.post)(handler)
            return True
    return False
