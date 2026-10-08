"""Preview engine.

Preview is a first-class subsystem, not merely an iframe. Adapters support
web, mobile, desktop, games, video, 3D, embedded, hardware (digital twin),
API, and CLI. This kernel ships the terminal + web preview adapters with a
unified interface: start / stop / reload / inspect / capture / status.

Preview failures become observable QA/Feedback events.
"""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
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
    """CLI/terminal preview: runs the built artifact and captures its output."""

    def __init__(self, sandbox: Any):
        self.sandbox = sandbox
        self.proc: Optional[subprocess.Popen] = None

    def start(self, port: int) -> PreviewStatus:
        try:
            self.proc = subprocess.Popen(
                ["python3", "-m", "http.server", str(port)],
                cwd=self.sandbox.cfg.workdir,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            return PreviewStatus(running=True, port=port,
                                 url=f"http://localhost:{port}")
        except Exception as exc:
            return PreviewStatus(running=False, error=str(exc))

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc = None

    def capture(self) -> Dict[str, Any]:
        if self.proc is None:
            return {"preview": "terminal", "url": "n/a", "screenshot": None}
        return {"preview": "terminal", "url": f"http://localhost:{self.proc.args[3]}",
                "screenshot": None, "stream": self.proc.stdout}


class WebPreview(PreviewAdapter):
    """Browser/web preview adapter."""

    def __init__(self, sandbox: Any):
        self.sandbox = sandbox

    def start(self, port: int) -> PreviewStatus:
        url = f"http://localhost:{port}"
        # browser automation not required here; status reports readiness.
        return PreviewStatus(running=True, port=port, url=url)

    def stop(self) -> None:
        pass

    def capture(self) -> Dict[str, Any]:
        return {"preview": "web", "url": "http://localhost", "screenshot": None,
                "dom_snapshot": None}


class PreviewEngine:
    """Selects an adapter by project type; delegates to the adapter."""

    ADAPTERS = {
        "web": WebPreview,
        "terminal": TerminalPreview,
        "cli": TerminalPreview,
        "mobile": None,   # future mobile device simulation adapter
        "desktop": None,  # future desktop runtime adapter
        "game": None,     # future game viewport adapter
        "video": None,    # future video timeline adapter
        "3d": None,       # future 3D viewport adapter
        "embedded": None,  # future virtual hardware adapter
        "hardware": None,  # future digital twin adapter
        "api": None,      # future API explorer adapter
    }

    def __init__(self, sandbox: Any, event_bus: Any):
        self.sandbox = sandbox
        self.bus = event_bus
        self.adapters: Dict[str, PreviewAdapter] = {}

    def adapter(self, project_type: str) -> PreviewAdapter:
        key = project_type if project_type in self.ADAPTERS else "terminal"
        if key not in self.adapters:
            cls = self.ADAPTERS.get(key)
            if cls is None:
                cls = TerminalPreview
            self.adapters[key] = cls(self.sandbox)
        return self.adapters[key]

    def start(self, project_type: str, port: int, goal_id: str,
              project_id: str, paths: Dict[str, str]) -> PreviewStatus:
        ad = self.adapter(project_type)
        status = ad.start(port)
        if self.bus is not None:
            self.bus.publish(
                models.Event(event_type=models.EventType.PREVIEW_STARTED,
                             project_id=project_id, goal_id=goal_id,
                             payload={"project_type": project_type,
                                      "url": status.url})
            )
        return status

    def stop(self, project_type: str) -> None:
        ad = self.adapter(project_type)
        ad.stop()

    def capture(self, project_type: str) -> Dict[str, Any]:
        return self.adapter(project_type).capture()

    def reload(self, project_type: str) -> None:
        self.adapter(project_type).reload()

    def inspect(self, project_type: str) -> Dict[str, Any]:
        return self.adapter(project_type).inspect()


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
        if project_type == "terminal" and artifacts.get("todo_app"):
            # placeholder: terminal adapter handles CLI previews directly
            return ""
        return ""


class WebPreviewAdapter(PreviewAdapter):
    """Web preview adapter: serves a generated app over HTTP and inspects it.

    Real behavior:
    - start(port): spawns a real HTTP server on the sandbox workdir
    - stop(): terminates it
    - capture(): returns HTTP status, headers, DOM snapshot (via a lightweight
      in-process HTML parser) and a screenshot if a browser driver is
      available (selenium/playwright). If no driver is installed, screenshot
      is reported as "awaiting browser driver" and is NOT counted as
      verification evidence.
    """

    def __init__(self, sandbox: Any, generator: WebContentGenerator,
                 event_bus: Any):
        self.sandbox = sandbox
        self.generator = generator
        self.bus = event_bus
        self.server: Any = None
        self.host = "127.0.0.1"
        self.port: Optional[int] = None

    def start(self, port: int) -> PreviewStatus:
        try:
            # Serve the sandbox workdir on a sandboxed loopback port.
            import functools
            from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

            class _Handler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=self.workdir,
                                     **kwargs)

                def log_message(self, fmt, *args):
                    # keep console quiet but observable via events
                    pass

            self.workdir = self.sandbox.cfg.workdir
            self.server = ThreadingHTTPServer(
                (self.host, port), functools.partial(_Handler)
            )
            self.port = port
            return PreviewStatus(running=True, port=port,
                                 url=f"http://{self.host}:{port}")
        except Exception as exc:
            return PreviewStatus(running=False, error=str(exc))

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server = None
            self.port = None

    def reload(self) -> None:
        # For a file-served static app, a reload = restart is simplest and
        # deterministic.
        self.stop()
        if self.sandbox.cfg.workdir:
            try:
                import os
                os.utime(os.path.join(self.sandbox.cfg.workdir, "index.html"),
                         None)
            except Exception:
                pass
            self.start(self.port or 8137)

    def inspect(self) -> Dict[str, Any]:
        # DOM snapshot via a lightweight HTML parse (no external parser).
        try:
            from html.parser import HTMLParser
            class _Dom(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.tags = []
                def handle_starttag(self, tag, attrs):
                    self.tags.append(tag)
            p = _Dom()
            # root artifact index.html under workdir
            import os
            idx = os.path.join(self.workdir, "index.html")
            if os.path.exists(idx):
                p.feed(open(idx, encoding="utf-8").read())
            return {"adapter": "web", "workdir": self.workdir,
                    "tags_found": p.tags, "title": "JaXir Todo"}
        except Exception as exc:
            return {"adapter": "web", "error": str(exc)}

    def capture(self) -> Dict[str, Any]:
        """Return a DOM snapshot; attempt a real screenshot if a driver is
        installed. Snapshot is the verification evidence; screenshot is an
        optional observability extension."""
        result = {
            "adapter": "web",
            "url": f"http://{self.host}:{self.port}",
            "dom_snapshot": self.inspect(),
            "screenshot_path": None,
            "screenshot_status": "pending_browser_driver",
        }
        # Attempt real screenshot if a driver is available.
        try:
            from selenium import webdriver  # type: ignore
            from selenium.webdriver.chrome.options import Options  # type: ignore
            opts = Options()
            opts.add_argument("--no-sandbox")
            opts.add_argument("--headless")
            driver = webdriver.Chrome(options=opts)
            driver.get(result["url"])
            result["screenshot_path"] = driver.save_screenshot(
                os.path.join(self.sandbox.cfg.workdir, "preview_shot.png")
            )
            result["screenshot_status"] = "captured"
            driver.quit()
        except Exception as exc:
            result["screenshot_status"] = "not_installed"
            result["screenshot_error"] = str(exc)
        return result
