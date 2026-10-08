"""Preview engine.

Preview is a first-class subsystem, not merely an iframe. Adapters support
web, mobile, desktop, games, video, 3D, embedded, hardware (digital twin),
API, and CLI. This kernel ships the terminal + web preview adapters with a
unified interface: start / stop / reload / inspect / capture / status.

Preview failures become observable QA/Feedback events.
"""

from __future__ import annotations

import os
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, Optional

from . import models


@dataclass
class PreviewStatus:
    running: bool = False
    port: Optional[int] = None
    url: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"running": self.running, "port": self.port,
                "url": self.url, "error": self.error}


class _HtmlInspector(HTMLParser):
    """Lightweight stdlib DOM inspector: tags, title, ids, input types."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list = []
        self.ids: list = []
        self.input_types: list = []
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append(tag)
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "input" and attrs.get("type"):
            self.input_types.append(attrs["type"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data.strip()


class PreviewAdapter(ABC):
    @abstractmethod
    def start(self, port: int) -> PreviewStatus:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    def reload(self) -> None:
        pass

    def inspect(self) -> Dict[str, Any]:
        return {"adapter": type(self).__name__}

    @abstractmethod
    def capture(self) -> Dict[str, Any]:
        pass

    def status(self) -> PreviewStatus:
        return PreviewStatus(running=False, url="")


class TerminalPreview(PreviewAdapter):
    """CLI/terminal preview: serves the built artifact over loopback."""

    def __init__(self, sandbox: Any, event_bus: Any = None):
        self.sandbox = sandbox
        self.bus = event_bus
        self.proc: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None

    def start(self, port: int = 0) -> PreviewStatus:
        try:
            self.proc = subprocess.Popen(
                ["python3", "-m", "http.server", str(port)],
                cwd=self.sandbox.cfg.workdir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            self.port = port
            return PreviewStatus(running=True, port=port,
                                 url=f"http://localhost:{port}")
        except Exception as exc:
            return PreviewStatus(running=False, error=str(exc))

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)
            self.proc = None

    def status(self) -> PreviewStatus:
        running = self.proc is not None and self.proc.poll() is None
        return PreviewStatus(running=running, port=self.port,
                             url=f"http://localhost:{self.port}" if running else "")

    def capture(self) -> Dict[str, Any]:
        if self.proc is None:
            return {"preview": "terminal", "url": "n/a", "screenshot": None}
        return {"preview": "terminal", "url": f"http://localhost:{self.port}",
                "screenshot": None, "stream": self.proc.stdout}


class WebPreview(PreviewAdapter):
    """Non-serving web placeholder: reports readiness without an HTTP server.

    Retained as an explicitly-labelled stub for project types that want a
    status-only web preview. Real web previews must use ``WebPreviewAdapter``,
    which serves the sandbox workdir and can be inspected/captured.
    """

    def __init__(self, sandbox: Any, event_bus: Any = None):
        self.sandbox = sandbox
        self.bus = event_bus
        self.port: Optional[int] = None

    def start(self, port: int = 0) -> PreviewStatus:
        self.port = port
        return PreviewStatus(running=True, port=port, url=f"http://localhost:{port}")

    def stop(self) -> None:
        self.port = None

    def capture(self) -> Dict[str, Any]:
        return {"preview": "web", "url": "http://localhost", "screenshot": None,
                "dom_snapshot": None, "stub": True}


class WebContentGenerator:
    """Generates web content for preview from JaXir artifacts.

    Currently self-contained for the Todo slice; extended with project-type
    adapters (web, mobile, desktop, games, video, 3D, embedded, hardware)
    without polluting the core preview subsystem.
    """

    @staticmethod
    def generate(project_type: str, project_id: str,
                 project_dir: str, artifacts: Dict[str, str]) -> str:
        """Returns the content to serve for a web preview."""
        if project_type == "web" and artifacts.get("todo_app"):
            # self-contained Todo web app (written by WebTodoApp).
            # Imported lazily: todoslice imports this module at load time.
            from .todoslice import WebTodoApp
            return WebTodoApp.WEB_APP
        return ""


class WebPreviewAdapter(PreviewAdapter):
    """Web preview adapter: serves the generated app over HTTP and inspects it.

    Real behavior:
    - start(port): serves the sandbox workdir from a real HTTP server on a
      loopback port (pass port=0 for an OS-assigned ephemeral port), with
      ``serve_forever`` running on a daemon thread
    - stop(): terminates the server and joins the thread
    - reload(): restarts the server so file changes are picked up
    - inspect(): DOM snapshot of ``index.html`` parsed with the stdlib
    - capture(): performs a real HTTP GET, recording status and the DOM
      snapshot of the served response; a screenshot is attempted only if a
      browser driver (selenium) is installed, otherwise it is reported as
      unavailable and is NOT counted as verification evidence
    """

    def __init__(self, sandbox: Any, event_bus: Any = None):
        self.sandbox = sandbox
        self.bus = event_bus
        self.server: Any = None
        self.thread: Any = None
        self.host = "127.0.0.1"
        self.port: Optional[int] = None
        self.workdir = str(getattr(sandbox.cfg, "workdir", "") or "")

    # -- interface -----------------------------------------------------

    def start(self, port: int = 0) -> PreviewStatus:
        try:
            import functools
            import threading
            from http.server import (SimpleHTTPRequestHandler,
                                     ThreadingHTTPServer)

            workdir = self.workdir

            class _QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, fmt, *args):  # keep console quiet
                    pass

            handler = functools.partial(_QuietHandler, directory=workdir)
            server = ThreadingHTTPServer((self.host, port), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.server, self.thread = server, thread
            self.port = server.server_address[1]
            return PreviewStatus(running=True, port=self.port, url=self.url())
        except Exception as exc:
            self.server, self.thread, self.port = None, None, None
            return PreviewStatus(running=False, error=str(exc))

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None
        self.port = None

    def reload(self) -> None:
        port = self.port
        self.stop()
        self.start(port or 0)

    def status(self) -> PreviewStatus:
        running = self.server is not None
        return PreviewStatus(running=running, port=self.port,
                             url=self.url() if running else "")

    def url(self) -> str:
        return f"http://{self.host}:{self.port}" if self.port else ""

    def inspect(self) -> Dict[str, Any]:
        """DOM snapshot of the sandbox's ``index.html`` (read from disk)."""
        index = os.path.join(self.workdir, "index.html")
        try:
            with open(index, encoding="utf-8") as f:
                html = f.read()
        except OSError as exc:
            return {"adapter": "web", "workdir": self.workdir, "error": str(exc)}
        return self._inspect_html(html)

    def capture(self) -> Dict[str, Any]:
        """Perform a real HTTP GET and record the served response."""
        result: Dict[str, Any] = {
            "adapter": "web",
            "url": self.url(),
            "http_status": None,
            "dom_snapshot": None,
            "screenshot_path": None,
            "screenshot_status": "pending_browser_driver",
        }
        if not self.port:
            result["http_error"] = "preview not running"
            return result

        # The served response - not the file on disk - is the evidence.
        try:
            with urllib.request.urlopen(self.url() + "/index.html",
                                        timeout=5) as resp:
                result["http_status"] = resp.getcode()
                result["dom_snapshot"] = self._inspect_html(
                    resp.read().decode("utf-8", "replace")
                )
        except (urllib.error.URLError, OSError) as exc:
            result["http_error"] = str(exc)
            return result

        # Screenshot is an optional observability extension.
        try:
            from selenium import webdriver  # type: ignore
            from selenium.webdriver.chrome.options import Options  # type: ignore
            opts = Options()
            opts.add_argument("--no-sandbox")
            opts.add_argument("--headless")
            driver = webdriver.Chrome(options=opts)
            driver.get(result["url"])
            result["screenshot_path"] = driver.save_screenshot(
                os.path.join(self.workdir, "preview_shot.png")
            )
            result["screenshot_status"] = "captured"
            driver.quit()
        except ImportError:
            result["screenshot_status"] = "not_installed"
        except Exception as exc:
            result["screenshot_status"] = "failed"
            result["screenshot_error"] = str(exc)
        return result

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _inspect_html(html: str) -> Dict[str, Any]:
        p = _HtmlInspector()
        p.feed(html)
        return {
            "adapter": "web",
            "title": p.title,
            "tags_found": p.tags,
            "ids": p.ids,
            "input_types": p.input_types,
            "bytes": len(html),
            "interactive": "form" in p.tags and "button" in p.tags,
        }


class PreviewEngine:
    """Selects an adapter by project type; delegates to the adapter."""

    ADAPTERS = {
        "web": WebPreviewAdapter,
        "terminal": TerminalPreview,
        "cli": TerminalPreview,
        "mobile": None,     # future mobile device simulation adapter
        "desktop": None,    # future desktop runtime adapter
        "game": None,       # future game viewport adapter
        "video": None,      # future video timeline adapter
        "3d": None,         # future 3D viewport adapter
        "embedded": None,   # future virtual hardware adapter
        "hardware": None,   # future digital twin adapter
        "api": None,        # future API explorer adapter
    }

    def __init__(self, sandbox: Any, event_bus: Any):
        self.sandbox = sandbox
        self.bus = event_bus
        self.adapters: Dict[str, PreviewAdapter] = {}

    def adapter(self, project_type: str) -> PreviewAdapter:
        key = project_type if project_type in self.ADAPTERS else "terminal"
        if key not in self.adapters:
            cls = self.ADAPTERS.get(key) or TerminalPreview
            self.adapters[key] = cls(self.sandbox, self.bus)
        return self.adapters[key]

    def start(self, project_type: str, port: int, goal_id: str,
              project_id: str, paths: Dict[str, str]) -> PreviewStatus:
        ad = self.adapter(project_type)
        status = ad.start(port)
        if self.bus is not None:
            # A preview that fails to come up is observable QA/Feedback input,
            # never a silent success.
            self.bus.publish(
                models.Event(
                    event_type=(models.EventType.PREVIEW_STARTED if status.running
                                else models.EventType.PREVIEW_FAILED),
                    project_id=project_id, goal_id=goal_id,
                    payload={"project_type": project_type, "url": status.url,
                             "running": status.running, "error": status.error},
                )
            )
        return status

    def stop(self, project_type: str) -> None:
        self.adapter(project_type).stop()

    def capture(self, project_type: str) -> Dict[str, Any]:
        return self.adapter(project_type).capture()

    def reload(self, project_type: str) -> None:
        self.adapter(project_type).reload()

    def inspect(self, project_type: str) -> Dict[str, Any]:
        return self.adapter(project_type).inspect()

    def status(self, project_type: str) -> PreviewStatus:
        return self.adapter(project_type).status()
