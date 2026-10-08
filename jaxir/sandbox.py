"""Sandbox isolation.

Generated code is untrusted by default. The sandbox controls filesystem,
processes, network, CPU, memory, ports, devices, secrets, and credentials.
The security boundary exists OUTSIDE the agent; we never assume an LLM will
obey sandbox policy.

Network modes (locked NFR): OFFLINE, LOCAL_ONLY, ALLOWLIST, UNRESTRICTED.

What is enforced here, concretely:

- filesystem: every path is resolved (symlinks followed) and must fall inside
  ``allowed_dirs``; traversal and symlink escapes are rejected.
- environment/secrets: child processes receive an allowlisted environment,
  secret-looking variables are dropped, and secret values are redacted from
  captured output.
- resources: ``RLIMIT_CPU``/``RLIMIT_AS``/``RLIMIT_NOFILE``/``RLIMIT_FSIZE``/
  ``RLIMIT_NPROC`` applied in the child, plus a wall-clock timeout.
- network: a policy gate over the four locked modes. See ``Sandbox.limitations``
  for exactly how far that gate reaches - it is NOT kernel-level isolation.
- permissions: capability-based grants; protected operations (constitution
  section 14) are denied unless explicitly granted, and denial is an event.

Requires POSIX for the resource limits; degrades to timeout-only elsewhere.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import models

try:  # POSIX resource limits (not available on Windows)
    import resource as _resource
except ImportError:  # pragma: no cover - platform dependent
    _resource = None


class NetworkMode(str):
    OFFLINE = "OFFLINE"
    LOCAL_ONLY = "LOCAL_ONLY"
    ALLOWLIST = "ALLOWLIST"
    UNRESTRICTED = "UNRESTRICTED"


#: Substrings that mark an environment variable as secret-bearing.
SECRET_MARKERS: Tuple[str, ...] = (
    "TOKEN", "SECRET", "PASSWORD", "PASSWD", "API_KEY", "APIKEY",
    "CREDENTIAL", "PRIVATE_KEY", "ACCESS_KEY", "CLIENT_SECRET",
)

#: Environment variables a child process is allowed to inherit.
DEFAULT_ENV_ALLOWLIST: Tuple[str, ...] = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "USER", "SHELL", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE",
)

#: Local hosts permitted under LOCAL_ONLY.
LOCAL_HOSTS: Tuple[str, ...] = ("localhost", "127.0.0.1", "::1", "0.0.0.0")

#: Operations that require explicit authorization (constitution section 14).
PROTECTED_OPERATIONS: Tuple[str, ...] = (
    "deploy.production",
    "infra.destroy",
    "delete.permanent",
    "payment.transaction",
    "external.communication",
    "hardware.physical",
    "manufacturing",
    "firmware.flash",
    "power.high_risk",
)


class SandboxViolation(Exception):
    """Base class for a denied sandbox action."""


class FilesystemViolation(SandboxViolation):
    pass


class NetworkViolation(SandboxViolation):
    pass


class PermissionDenied(SandboxViolation):
    pass


@dataclass
class SandboxConfig:
    project_root: str
    workdir: str
    network_mode: str = NetworkMode.LOCAL_ONLY
    allowed_dirs: Optional[List[str]] = None
    cpu_limit: Optional[int] = None          # seconds of CPU time (RLIMIT_CPU)
    memory_limit_mb: Optional[int] = None    # address space (RLIMIT_AS)
    time_limit_s: Optional[int] = 20         # wall clock
    max_file_size_mb: Optional[int] = None   # RLIMIT_FSIZE
    max_processes: Optional[int] = None      # RLIMIT_NPROC
    max_open_files: int = 256                # RLIMIT_NOFILE
    allowed_hosts: Optional[List[str]] = None  # for ALLOWLIST
    env_allowlist: Optional[List[str]] = None

    def __post_init__(self):
        self.project_root = str(Path(self.project_root).resolve())
        self.workdir = str(Path(self.workdir).resolve())
        if self.allowed_dirs is None:
            self.allowed_dirs = [self.project_root, self.workdir]
        self.allowed_dirs = [str(Path(d).resolve()) for d in self.allowed_dirs]
        if self.allowed_hosts is None:
            self.allowed_hosts = []
        if self.env_allowlist is None:
            self.env_allowlist = list(DEFAULT_ENV_ALLOWLIST)


class NetworkPolicy:
    """The four locked network modes, applied to a host/URL."""

    def __init__(self, mode: str, allowed_hosts: Optional[Iterable[str]] = None):
        self.mode = mode
        self.allowed_hosts = list(allowed_hosts or [])

    def permits_host(self, host: str) -> bool:
        host = (host or "").lower().strip("[]")
        if self.mode == NetworkMode.UNRESTRICTED:
            return True
        if self.mode == NetworkMode.OFFLINE:
            return False
        if self.mode == NetworkMode.LOCAL_ONLY:
            return host in LOCAL_HOSTS
        if self.mode == NetworkMode.ALLOWLIST:
            return any(
                host == pattern.lower() or
                (pattern.startswith("*.") and host.endswith(pattern[1:]))
                for pattern in self.allowed_hosts
            )
        return False

    def permits_url(self, url: str) -> bool:
        from urllib.parse import urlparse
        return self.permits_host(urlparse(url).hostname or "")

    def check_url(self, url: str) -> None:
        if not self.permits_url(url):
            raise NetworkViolation(
                f"network mode {self.mode} denies {url!r}"
                + (f" (allowlist: {self.allowed_hosts})"
                   if self.mode == NetworkMode.ALLOWLIST else "")
            )

    def child_env(self, env: Dict[str, str]) -> Dict[str, str]:
        """Belt-and-braces for child processes.

        Kernel isolation is out of scope (see ``Sandbox.limitations``), so for
        the restrictive modes we also point the well-known proxy variables at a
        black hole and disable SSL certificate verification backdoors. This
        stops well-behaved HTTP clients; it is not a security boundary.
        """
        if self.mode in (NetworkMode.OFFLINE, NetworkMode.ALLOWLIST):
            env = dict(env)
            env["http_proxy"] = env["https_proxy"] = "http://127.0.0.1:9"
            env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:9"
            env["no_proxy"] = env["NO_PROXY"] = ""
        return env


@dataclass
class PermissionBroker:
    """Capability-based permissions for sensitive actions (sections 14/43).

    Denials are events, never silent failures.
    """

    event_bus: Any = None
    granted: List[str] = field(default_factory=list)

    def can(self, operation: str) -> bool:
        if operation not in PROTECTED_OPERATIONS:
            return True
        return operation in self.granted

    def grant(self, operation: str) -> None:
        if operation not in self.granted:
            self.granted.append(operation)

    def revoke(self, operation: str) -> None:
        if operation in self.granted:
            self.granted.remove(operation)

    def require(self, operation: str, project_id: str = "", goal_id: Optional[str] = None,
                resource: str = "", reason: str = "") -> None:
        """Raise ``PermissionDenied`` unless the operation is authorized."""
        if operation in PROTECTED_OPERATIONS and self.event_bus is not None:
            self.event_bus.publish(
                models.Event(
                    event_type=models.EventType.PERMISSION_REQUESTED,
                    project_id=project_id, goal_id=goal_id,
                    payload={"operation": operation, "resource": resource,
                             "reason": reason},
                )
            )
        allowed = self.can(operation)
        if self.event_bus is not None and operation in PROTECTED_OPERATIONS:
            self.event_bus.publish(
                models.Event(
                    event_type=(models.EventType.PERMISSION_GRANTED if allowed
                                else models.EventType.PERMISSION_DENIED),
                    project_id=project_id, goal_id=goal_id,
                    payload={"operation": operation, "resource": resource},
                )
            )
        if not allowed:
            raise PermissionDenied(
                f"operation {operation!r} requires authorization by the configured "
                f"policy (protected operation, constitution section 14)"
            )


class Sandbox:
    """Enforces the policy in ``SandboxConfig`` around untrusted generated code."""

    def __init__(self, config: SandboxConfig, event_bus: Any):
        self.cfg = config
        self.bus = event_bus
        self.network = NetworkPolicy(config.network_mode, config.allowed_hosts)
        self.broker = PermissionBroker(event_bus)
        self._children: List[subprocess.Popen] = []
        self._secrets: List[str] = self._discover_secrets()

    # ------------------------------------------------------------------
    # Capability disclosure (honest, not aspirational)
    # ------------------------------------------------------------------

    @property
    def limitations(self) -> List[str]:
        out = []
        if _resource is None:
            out.append("resource limits unavailable on this platform (POSIX only)")
        if self.network.mode in (NetworkMode.OFFLINE, NetworkMode.ALLOWLIST):
            out.append(
                "network isolation is enforced at the sandbox call boundary "
                "and via proxy variables only; it is NOT kernel-level isolation "
                "and cannot stop a raw socket"
            )
        if self.network.mode == NetworkMode.UNRESTRICTED:
            out.append("network access is unrestricted by policy")
        return out

    def describe(self) -> Dict[str, Any]:
        return {
            "network_mode": self.network.mode,
            "allowed_dirs": list(self.cfg.allowed_dirs),
            "allowed_hosts": list(self.cfg.allowed_hosts),
            "limits": {
                "cpu_s": self.cfg.cpu_limit,
                "memory_mb": self.cfg.memory_limit_mb,
                "wall_clock_s": self.cfg.time_limit_s,
                "max_file_size_mb": self.cfg.max_file_size_mb,
                "max_processes": self.cfg.max_processes,
                "max_open_files": self.cfg.max_open_files,
            },
            "secrets_scrubbed": len(self._secrets),
            "limitations": self.limitations,
        }

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------

    @staticmethod
    def _is_secret_name(name: str) -> bool:
        upper = name.upper()
        return any(marker in upper for marker in SECRET_MARKERS)

    def _discover_secrets(self) -> List[str]:
        """Values of secret-looking environment variables (never logged)."""
        found = []
        for name, value in os.environ.items():
            if self._is_secret_name(name) and value and len(value) >= 6:
                found.append(value)
        return found

    def register_secret(self, value: str) -> None:
        if value and len(value) >= 6 and value not in self._secrets:
            self._secrets.append(value)

    def redact(self, text: Optional[str]) -> str:
        """Remove registered secret values from captured output."""
        if not text:
            return ""
        for secret in self._secrets:
            text = text.replace(secret, "***REDACTED***")
        return text

    def child_env(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """A scrubbed environment: allowlist only, secrets dropped."""
        allowed = set(self.cfg.env_allowlist or ())
        env = {k: v for k, v in os.environ.items()
               if k in allowed and not self._is_secret_name(k)}
        env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
        if extra:
            env.update({k: str(v) for k, v in extra.items()})
        return self.network.child_env(env)

    # ------------------------------------------------------------------
    # Filesystem
    # ------------------------------------------------------------------

    def resolve(self, path: Any, must_exist: bool = False) -> Path:
        """Resolve ``path`` and require it to stay inside ``allowed_dirs``."""
        p = Path(path)
        if not p.is_absolute():
            p = Path(self.cfg.workdir) / p
        resolved = p.resolve()
        for root in self.cfg.allowed_dirs:
            try:
                resolved.relative_to(Path(root))
                if must_exist and not resolved.exists():
                    raise FilesystemViolation(f"{resolved} does not exist")
                return resolved
            except ValueError:
                continue
        raise FilesystemViolation(
            f"path {resolved} escapes the sandbox "
            f"(allowed: {self.cfg.allowed_dirs})"
        )

    def prepare_workdir(self, task: Any) -> Path:
        """Create an isolated, sandboxed working directory for a task."""
        workdir = self.resolve(Path(self.cfg.workdir) / str(getattr(task, "task_id", "task")))
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    def write_file(self, path: Path, content: str) -> Path:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def read_file(self, path: Path) -> str:
        return self.resolve(path, must_exist=True).read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Network (the enforced call boundary)
    # ------------------------------------------------------------------

    def require_network(self, url: str) -> None:
        self.network.check_url(url)

    def open_url(self, url: str, timeout: int = 10) -> Any:
        """Fetch ``url`` through the network policy gate."""
        self.require_network(url)
        import urllib.request
        return urllib.request.urlopen(url, timeout=timeout)  # noqa: S310

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def require_permission(self, operation: str, **kwargs: Any) -> None:
        self.broker.require(operation, **kwargs)

    # ------------------------------------------------------------------
    # Processes
    # ------------------------------------------------------------------

    def _preexec(self) -> Optional[Callable[[], None]]:
        """Build the child pre-exec hook that applies resource limits."""
        if _resource is None:
            return None
        cfg = self.cfg

        def _apply() -> None:  # pragma: no cover - runs in the child
            limits = [
                (_resource.RLIMIT_CPU,
                 cfg.cpu_limit if cfg.cpu_limit else None),
                (_resource.RLIMIT_AS,
                 cfg.memory_limit_mb * 1024 * 1024 if cfg.memory_limit_mb else None),
                (_resource.RLIMIT_FSIZE,
                 cfg.max_file_size_mb * 1024 * 1024 if cfg.max_file_size_mb else None),
                (_resource.RLIMIT_NOFILE, cfg.max_open_files or None),
                (_resource.RLIMIT_NPROC, cfg.max_processes or None),
            ]
            for which, value in limits:
                if value is None:
                    continue
                try:
                    soft, hard = _resource.getrlimit(which)
                    value = max(1, int(value))
                    if hard != _resource.RLIM_INFINITY:
                        value = min(value, hard)
                    _resource.setrlimit(which, (value, hard))
                except (ValueError, OSError):
                    pass

        return _apply

    def run(self, cmd: List[str], timeout: Optional[int] = None,
            env: Optional[Dict[str, str]] = None,
            cwd: Optional[str] = None) -> Dict[str, Any]:
        """Run a command inside the sandbox: confined cwd, scrubbed env,
        resource limits, wall-clock timeout."""
        start = time.perf_counter()
        result: Dict[str, Any] = {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "reason": None,
            "duration_ms": 0.0,
        }
        try:
            workdir = self.resolve(cwd or self.cfg.workdir)
        except FilesystemViolation as exc:
            result["reason"] = f"filesystem: {exc}"
            self._emit(models.EventType.BUILD_FAILED, {"reason": result["reason"]})
            return result

        effective_timeout = timeout if timeout is not None else self.cfg.time_limit_s
        child_env = self.child_env(env)
        self._emit(models.EventType.BUILD_STARTED,
                   {"command": " ".join(shlex.quote(c) for c in cmd),
                    "cwd": str(workdir), "network_mode": self.network.mode})
        try:
            # start_new_session puts the child in its own process group so a
            # timeout can kill the whole tree instead of orphaning grandchildren.
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(workdir),
                env=child_env,
                preexec_fn=self._preexec(),
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            result["reason"] = f"spawn failed: {exc}"
            self._emit(models.EventType.BUILD_FAILED, {"reason": result["reason"]})
            return result

        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            self._kill_group(proc)
            stdout, stderr = proc.communicate()
            result["reason"] = f"timeout after {effective_timeout}s"
        except (OSError, ValueError) as exc:
            self._kill_group(proc)
            stdout, stderr = "", ""
            result["reason"] = f"execution failed: {exc}"

        result["returncode"] = proc.returncode
        result["stdout"] = self.redact(_as_text(stdout))
        result["stderr"] = self.redact(_as_text(stderr))
        if result["reason"] is None:
            result["ok"] = proc.returncode == 0
            if not result["ok"]:
                result["reason"] = f"exit code {proc.returncode}"

        result["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
        result["limits"] = self.describe()["limits"]
        self._emit(
            models.EventType.BUILD_COMPLETED if result["ok"] else models.EventType.BUILD_FAILED,
            {"returncode": result["returncode"], "reason": result["reason"],
             "stdout": result["stdout"][-2000:], "stderr": result["stderr"][-1000:]},
        )
        return result

    def run_shell(self, cmd: str, timeout: Optional[int] = None,
                  env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        return self.run(shlex.split(cmd), timeout=timeout, env=env)

    def spawn(self, cmd: List[str], env: Optional[Dict[str, str]] = None,
              cwd: Optional[str] = None) -> subprocess.Popen:
        """Start a tracked background process (ends up in orphan detection)."""
        workdir = self.resolve(cwd or self.cfg.workdir)
        proc = subprocess.Popen(
            cmd, cwd=str(workdir), env=self.child_env(env),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        self._children.append(proc)
        return proc

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        """SIGKILL the child's whole process group, not just the child."""
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:
                pass

    def detect_orphans(self) -> List[Dict[str, Any]]:
        """Background processes that outlived their task (section 45)."""
        return [
            {"pid": p.pid, "cmd": " ".join(shlex.quote(c) for c in (p.args or []))
             if isinstance(p.args, (list, tuple)) else str(p.args)}
            for p in self._children if p.poll() is None
        ]

    def reap(self) -> List[int]:
        """Terminate tracked children; returns the pids that were killed."""
        killed = []
        for p in self._children:
            if p.poll() is None:
                self._kill_group(p)
                try:
                    p.wait(timeout=5)
                except Exception:
                    continue
                killed.append(p.pid)
        self._children = [p for p in self._children if p.poll() is None]
        return killed

    # ------------------------------------------------------------------
    # Cleanup / governance
    # ------------------------------------------------------------------

    def cleanup(self, paths: List[str]) -> None:
        """Remove paths, but only inside the sandbox (never a wider rmtree)."""
        for raw in paths:
            try:
                path = self.resolve(raw)
            except FilesystemViolation:
                continue
            if path in (Path(self.cfg.project_root), Path(self.cfg.workdir)):
                continue  # never delete the sandbox roots themselves
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _emit(self, event_type: models.EventType, payload: Dict[str, Any]) -> None:
        if self.bus is not None and hasattr(self.bus, "publish"):
            self.bus.publish(
                models.Event(event_type=event_type, project_id="sandbox",
                             payload=payload)
            )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
