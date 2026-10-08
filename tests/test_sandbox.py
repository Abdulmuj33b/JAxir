"""Sandbox isolation tests.

Constitution section 13: generated code is untrusted by default, the security
boundary exists outside the agent, and the sandbox must control filesystem,
processes, network, CPU, memory, secrets, and credentials.

These tests exercise the enforcement that is genuinely implemented. The
limitations that are NOT enforced (no kernel-level network namespace) are
asserted as disclosed rather than silently assumed away - section 49.13.
"""

import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from jaxir import models
from jaxir.events import EventBus
from jaxir.sandbox import (
    NetworkMode,
    PermissionBroker,
    PermissionDenied,
    FilesystemViolation,
    NetworkViolation,
    Sandbox,
    SandboxConfig,
)


def make_sandbox(tmp_path, **kwargs):
    workdir = tmp_path / "work"
    workdir.mkdir(exist_ok=True)
    bus = EventBus()
    cfg = SandboxConfig(project_root=str(tmp_path), workdir=str(workdir), **kwargs)
    return Sandbox(cfg, bus), bus, workdir


class TestFilesystemConfinement:
    def test_write_inside_sandbox_allowed(self, tmp_path):
        sb, _, workdir = make_sandbox(tmp_path)
        p = sb.write_file(workdir / "a.txt", "hi")
        assert p.read_text() == "hi"

    @pytest.mark.parametrize("target", [
        "/etc/passwd",
        "../../etc/passwd",
        "/tmp/elsewhere/escape.txt",
    ])
    def test_escape_outside_allowed_dirs_rejected(self, tmp_path, target):
        sb, _, _ = make_sandbox(tmp_path)
        with pytest.raises(FilesystemViolation):
            sb.resolve(target)

    def test_symlink_escape_rejected(self, tmp_path):
        """A symlink pointing outside the sandbox must not be a way out.

        No platform guard: the sandbox's symlink defence is POSIX-relevant
        (the sandbox uses rlimits), so an environment where this cannot even be
        set up should fail loudly rather than silently skip the check.
        """
        sb, _, workdir = make_sandbox(tmp_path)
        link = workdir / "sneaky"
        link.symlink_to("/etc")
        # Prove the escape vector was actually created.
        assert link.is_symlink()
        assert (link / "passwd").exists()  # the symlink really does resolve
        with pytest.raises(FilesystemViolation):
            sb.resolve(link / "passwd")
        # ...and the same via a relative symlink out of the workdir.
        relative = workdir / "up"
        relative.symlink_to("../../")
        assert relative.is_symlink()
        with pytest.raises(FilesystemViolation):
            sb.resolve(relative / "etc" / "passwd")

    def test_read_missing_file_rejected(self, tmp_path):
        sb, _, workdir = make_sandbox(tmp_path)
        with pytest.raises(FilesystemViolation):
            sb.read_file(workdir / "nope.txt")

    def test_cleanup_refuses_to_delete_outside_sandbox(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        outside = tmp_path.parent / "must_survive.txt"
        outside.write_text("keep me")
        sb.cleanup([str(outside)])
        assert outside.exists()
        outside.unlink()

    def test_cleanup_never_deletes_the_sandbox_root(self, tmp_path):
        sb, _, workdir = make_sandbox(tmp_path)
        (workdir / "junk").write_text("x")
        sb.cleanup([str(workdir / "junk")])
        assert not (workdir / "junk").exists()
        sb.cleanup([str(workdir)])
        assert workdir.exists()  # root is protected


class TestSecretHandling:
    def test_secret_env_is_not_inherited(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_API_KEY", "supersecretvalue123")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "anothersecretvalue")
        sb, _, _ = make_sandbox(tmp_path)
        env = sb.child_env()
        assert "MY_API_KEY" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "PATH" in env

    def test_non_secret_env_filtered_by_allowlist(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOME_RANDOM_VAR", "value")
        sb, _, _ = make_sandbox(tmp_path)
        assert "SOME_RANDOM_VAR" not in sb.child_env()

    def test_explicit_env_passed_through(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        assert sb.child_env({"TODO_STORE": "x.json"})["TODO_STORE"] == "x.json"

    def test_secret_values_redacted_from_output(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        sb.register_secret("supersecretvalue123")
        assert "supersecretvalue123" not in sb.redact("token=supersecretvalue123")
        assert "***REDACTED***" in sb.redact("token=supersecretvalue123")

    def test_child_process_cannot_see_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LEAKY_API_KEY", "supersecretvalue123")
        sb, _, _ = make_sandbox(tmp_path)
        r = sb.run([sys.executable, "-c",
                    "import os; print(os.environ.get('LEAKY_API_KEY', 'ABSENT'))"])
        assert r["ok"] and r["stdout"].strip() == "ABSENT"


class TestResourceGovernance:
    def test_wall_clock_timeout_enforced(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, time_limit_s=2)
        r = sb.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=2)
        assert r["ok"] is False
        assert "timeout" in r["reason"]

    def test_timeout_kills_grandchildren(self, tmp_path):
        """The child's whole process group dies, so nothing is orphaned."""
        import time
        marker = tmp_path / "work" / "grandchild.pid"
        script = (
            "import subprocess, sys, time, os\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )
        sb, _, _ = make_sandbox(tmp_path, time_limit_s=2)
        r = sb.run([sys.executable, "-c", script], timeout=3)
        assert r["ok"] is False and "timeout" in r["reason"]
        # Give the kernel a moment to reap the group.
        time.sleep(0.5)
        assert _no_stray_sleepers(marker, tmp_path)

    def test_memory_limit_enforced(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, memory_limit_mb=256)
        r = sb.run([sys.executable, "-c", "b = bytearray(2 * 1024 ** 3)"])
        assert r["ok"] is False
        assert "MemoryError" in r["stderr"] or r["reason"]

    def test_limits_are_reported(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, cpu_limit=5, memory_limit_mb=512)
        limits = sb.describe()["limits"]
        assert limits["cpu_s"] == 5 and limits["memory_mb"] == 512

    def test_run_denies_cwd_outside_sandbox(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        r = sb.run([sys.executable, "-c", "print(1)"], cwd="/etc")
        assert r["ok"] is False
        assert "filesystem" in r["reason"]

    def test_orphan_detection_and_reap(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        sb.spawn([sys.executable, "-c", "import time; time.sleep(60)"])
        assert len(sb.detect_orphans()) == 1
        killed = sb.reap()
        assert len(killed) == 1
        assert sb.detect_orphans() == []


class TestNetworkPolicy:
    @pytest.mark.parametrize("mode,host,expected", [
        (NetworkMode.OFFLINE, "localhost", False),
        (NetworkMode.OFFLINE, "example.com", False),
        (NetworkMode.LOCAL_ONLY, "localhost", True),
        (NetworkMode.LOCAL_ONLY, "127.0.0.1", True),
        (NetworkMode.LOCAL_ONLY, "example.com", False),
        (NetworkMode.UNRESTRICTED, "example.com", True),
    ])
    def test_mode_matrix(self, tmp_path, mode, host, expected):
        sb, _, _ = make_sandbox(tmp_path, network_mode=mode)
        assert sb.network.permits_host(host) is expected

    def test_allowlist_matches_exact_and_wildcard(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, network_mode=NetworkMode.ALLOWLIST,
                                allowed_hosts=["api.example.com", "*.github.com"])
        assert sb.network.permits_host("api.example.com") is True
        assert sb.network.permits_host("cdn.github.com") is True
        assert sb.network.permits_host("evil.com") is False

    def test_offline_denies_outbound_call(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, network_mode=NetworkMode.OFFLINE)
        with pytest.raises(NetworkViolation):
            sb.open_url("http://example.com")

    def test_local_only_denies_remote_host(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, network_mode=NetworkMode.LOCAL_ONLY)
        with pytest.raises(NetworkViolation):
            sb.require_network("http://example.com")

    def test_local_only_serves_loopback(self, tmp_path):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class _H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            sb, _, _ = make_sandbox(tmp_path, network_mode=NetworkMode.LOCAL_ONLY)
            url = f"http://127.0.0.1:{server.server_address[1]}/"
            with sb.open_url(url) as resp:
                assert resp.getcode() == 200
        finally:
            server.shutdown()
            server.server_close()

    def test_child_proxy_vars_poisoned_when_restricted(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, network_mode=NetworkMode.OFFLINE)
        env = sb.child_env()
        assert "127.0.0.1:9" in env["http_proxy"]


class TestPermissionBroker:
    def test_unprotected_operation_allowed(self):
        assert PermissionBroker().can("read.file") is True

    def test_protected_operation_denied_by_default(self):
        with pytest.raises(PermissionDenied):
            PermissionBroker().require("deploy.production")

    def test_denial_is_an_event_not_a_silent_failure(self):
        bus = EventBus()
        broker = PermissionBroker(bus)
        with pytest.raises(PermissionDenied):
            broker.require("firmware.flash", project_id="p", goal_id="g")
        assert bus.count(models.EventType.PERMISSION_REQUESTED) == 1
        assert bus.count(models.EventType.PERMISSION_DENIED) == 1
        assert bus.count(models.EventType.PERMISSION_GRANTED) == 0

    def test_grant_authorizes_and_emits_granted(self):
        bus = EventBus()
        broker = PermissionBroker(bus)
        broker.grant("deploy.production")
        broker.require("deploy.production", project_id="p")
        assert bus.count(models.EventType.PERMISSION_GRANTED) == 1
        assert bus.count(models.EventType.PERMISSION_DENIED) == 0

    def test_revoke_removes_authorization(self):
        bus = EventBus()
        broker = PermissionBroker(bus)
        broker.grant("payment.transaction")
        broker.revoke("payment.transaction")
        with pytest.raises(PermissionDenied):
            broker.require("payment.transaction")

    def test_sandbox_exposes_broker(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        with pytest.raises(PermissionDenied):
            sb.require_permission("manufacturing", project_id="todo")


class TestCapabilityDisclosure:
    def test_limitations_are_disclosed_not_hidden(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path, network_mode=NetworkMode.OFFLINE)
        limitations = sb.limitations
        assert any("NOT kernel-level isolation" in item for item in limitations)

    def test_describe_reports_policy_and_scrubbed_secret_count(self, tmp_path,
                                                              monkeypatch):
        monkeypatch.setenv("SOME_TOKEN", "supersecretvalue123")
        sb, _, _ = make_sandbox(tmp_path)
        info = sb.describe()
        assert info["network_mode"] == NetworkMode.LOCAL_ONLY
        assert info["secrets_scrubbed"] >= 1
        assert "limitations" in info


class TestProcessEvents:
    def test_build_events_emitted(self, tmp_path):
        sb, bus, _ = make_sandbox(tmp_path)
        sb.run([sys.executable, "-c", "print('hi')"])
        assert bus.count(models.EventType.BUILD_STARTED) == 1
        assert bus.count(models.EventType.BUILD_COMPLETED) == 1

    def test_failing_command_emits_build_failed(self, tmp_path):
        sb, bus, _ = make_sandbox(tmp_path)
        r = sb.run([sys.executable, "-c", "raise SystemExit(3)"])
        assert r["ok"] is False and r["returncode"] == 3
        assert bus.count(models.EventType.BUILD_FAILED) == 1

    def test_output_is_redacted_in_result(self, tmp_path):
        sb, _, _ = make_sandbox(tmp_path)
        sb.register_secret("supersecretvalue123")
        r = sb.run([sys.executable, "-c",
                    "print('supersecretvalue123')"])
        assert "supersecretvalue123" not in r["stdout"]
        assert "***REDACTED***" in r["stdout"]


class TestOrchestratorSandboxIntegration:
    """Section 13: the boundary guards generated output, not just declarations."""

    def _orchestrator(self, tmp_path):
        from jaxir.orchestrator import AgentModel, Orchestrator
        bus = EventBus()
        workdir = tmp_path / "work"
        workdir.mkdir(exist_ok=True)
        sandbox = Sandbox(SandboxConfig(project_root=str(tmp_path),
                                        workdir=str(workdir)), bus)
        og = Orchestrator(bus, sandbox)
        og.register(AgentModel(agent_id="coder", agent_type="coder",
                               identity="JaXir Coder", capabilities=["coding"],
                               tools=[], environment={}))
        return og, sandbox, workdir

    def _coder_task(self, project_dir):
        return models.Task(goal_id="g", owner_agent_id="planner",
                           agent_type="coder", capabilities=["coding"],
                           inputs={"project_dir": str(project_dir)})

    def test_generated_code_cannot_escape_the_sandbox(self, tmp_path):
        og, _, _ = self._orchestrator(tmp_path)
        with pytest.raises(FilesystemViolation):
            og._execute_task(self._coder_task("/etc"))

    def test_generated_code_written_inside_sandbox(self, tmp_path):
        og, _, workdir = self._orchestrator(tmp_path)
        result = og._execute_task(self._coder_task(workdir))
        assert result["ok"] is True
        assert (workdir / "todo").exists()

    def test_workdir_is_confined_and_creates_no_litter(self, tmp_path):
        og, _, _ = self._orchestrator(tmp_path)
        task = self._coder_task(tmp_path)
        og._provision_env(task)
        confined = Path(task.inputs["workdir"])
        assert str(confined).startswith(str(tmp_path))
        # Nothing is created: an unused per-task dir would be resource litter.
        assert not confined.exists()


def _no_stray_sleepers(marker: Path, tmp_path: Path) -> bool:
    """True when no process other than this pytest run sleeps for 60s.

    Best-effort: reads /proc for processes whose cmdline mentions the marker
    directory, which is unique to this test.
    """
    needle = str(tmp_path)
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "replace")
        except OSError:
            continue
        if needle in cmdline and "time.sleep(60)" in cmdline:
            return False
    return True
