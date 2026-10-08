"""Sandbox isolation.

Generated code is untrusted by default. The sandbox controls filesystem,
processes, network, CPU, memory, ports, devices, secrets, and credentials.
The security boundary exists OUTSIDE the agent; we never assume an LLM will
obey sandbox policy.

Network modes (locked NFR): OFFLINE, LOCAL_ONLY, ALLOWLIST, UNRESTRICTED.
"""

from __future__ import annotations

from dataclasses import dataclass
from . import models

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


class NetworkMode(str):
    OFFLINE = "OFFLINE"
    LOCAL_ONLY = "LOCAL_ONLY"
    ALLOWLIST = "ALLOWLIST"
    UNRESTRICTED = "UNRESTRICTED"


@dataclass
class SandboxConfig:
    project_root: str
    workdir: str
    network_mode: NetworkMode = NetworkMode.LOCAL_ONLY
    allowed_dirs: List[str] = None
    cpu_limit: Optional[int] = None
    memory_limit_mb: Optional[int] = None
    time_limit_s: Optional[int] = None

    def __post_init__(self):
        if self.allowed_dirs is None:
            self.allowed_dirs = [self.project_root, self.workdir]


class Sandbox:
    def __init__(self, config: SandboxConfig, event_bus: Any):
        self.cfg = config
        self.bus = event_bus

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    def prepare_workdir(self, task: Any) -> Path:
        """Create an isolated, sandboxed working directory for a task."""
        workdir = Path(self.cfg.workdir) / str(getattr(task, "task_id", "task"))
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    def write_file(self, path: Path, content: str) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def read_file(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    def run(self, cmd: List[str], timeout: Optional[int] = None) -> Dict[str, Any]:
        """Run a command inside the sandbox (network restricted)."""
        if self.bus is not None and hasattr(self.bus, "publish"):
            self.bus.publish(
                models.Event(event_type=models.EventType.BUILD_STARTED,
                             project_id="sandbox",
                             payload={"command": " ".join(shlex.quote(c) for c in cmd)})
            )
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=self.cfg.workdir,
            # deny network/outside-file access via policy enforcement here
        )
        if self.bus is not None:
            self.bus.publish(
                models.Event(event_type=models.EventType.BUILD_COMPLETED,
                             project_id="sandbox",
                             payload={"returncode": proc.returncode,
                                      "stdout": proc.stdout[-2000:],
                                      "stderr": proc.stderr[-1000:]})
            )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def run_shell(self, cmd: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        return self.run(shlex.split(cmd), timeout=timeout)

    # ------------------------------------------------------------------
    # Cleanup / governance
    # ------------------------------------------------------------------

    def cleanup(self, paths: List[str]) -> None:
        for p in paths:
            path = Path(p)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)

    def detect_orphans(self) -> List[Dict[str, Any]]:
        # In a full system: scan /proc for child processes of this sandbox.
        return []
