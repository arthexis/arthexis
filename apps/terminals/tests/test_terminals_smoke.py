import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.core.admin import OwnableAdminForm
from apps.groups.models import SecurityGroup
from apps.terminals import tasks
from apps.terminals.admin import AgentTerminalAdmin
from apps.terminals.models import AgentTerminal

User = get_user_model()


def test_assigned_to_any_user_resolves_direct_and_group_assignments(db):
    owner = User.objects.create_user(username="terminal-owner")
    group = SecurityGroup.objects.create(name="terminal-ops")
    group.user_set.add(owner)
    direct = AgentTerminal.objects.create(name="direct", user=owner)
    grouped = AgentTerminal.objects.create(name="grouped", group=group)

    matched_ids = set(AgentTerminal.assigned_to_any_user().values_list("id", flat=True))

    assert direct.id in matched_ids
    assert grouped.id in matched_ids


def test_admin_disables_add_permission(db):
    admin = AgentTerminalAdmin(AgentTerminal, AdminSite())
    request = RequestFactory().get("/admin/terminals/agentterminal/")
    request.user = User.objects.create_superuser(username="root", password="secret")

    assert admin.has_add_permission(request) is False


def test_launch_terminal_uses_powershell_script_on_windows(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "_is_windows", lambda: True)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: None)
    launched = {}

    class FakeProcess:
        pid = 1234

    def fake_popen(command):
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(tasks.subprocess, "Popen", fake_popen)
    terminal = AgentTerminal(name="windows-terminal", launch_command="echo ready")

    tasks._launch_terminal(terminal)

    assert launched["command"][:4] == [
        "powershell.exe",
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
    ]
    script_path = tmp_path / "scripts" / "None.ps1"
    assert script_path.read_text(encoding="utf-8") == "echo ready"
    assert (tmp_path / "None.pid").exists()


def test_launch_command_in_terminal_builds_windows_codex_command(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "_is_windows", lambda: True)
    monkeypatch.setattr(
        tasks.shutil,
        "which",
        lambda name: "wt.exe" if name == "wt.exe" else None,
    )
    launched = {}

    class FakeProcess:
        pid = 5678

    def fake_popen(command):
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(tasks.subprocess, "Popen", fake_popen)

    pid_file = tasks.launch_command_in_terminal(
        ["codex", "[SECRETARY] Mara:\nOperator request"],
        title="Arthexis Secretary",
        state_key="whatsapp-secretary",
        working_directory=tmp_path / "repo",
    )

    assert launched["command"][:4] == ["wt.exe", "new-tab", "--title", "Arthexis Secretary"]
    assert pid_file == tmp_path / "whatsapp-secretary.pid"
    script = (tmp_path / "scripts" / "whatsapp-secretary.ps1").read_text(encoding="utf-8")
    assert "Set-Location -LiteralPath" in script
    assert "& 'codex' '[SECRETARY] Mara:" in script


def test_launch_command_in_terminal_uses_script_file_on_posix(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "_is_windows", lambda: False)
    launched = {}

    class FakeProcess:
        pid = 9999

    def fake_popen(command):
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(tasks.subprocess, "Popen", fake_popen)

    pid_file = tasks.launch_command_in_terminal(
        ["echo", "super-secret-value"],
        state_key="linux-secret",
    )

    script_path = tmp_path / "scripts" / "linux-secret.sh"
    assert script_path.exists()
    assert launched["command"][-3:] == ["sh", "-lc", f". {tasks.shlex.quote(str(script_path))}"]
    assert "super-secret-value" not in " ".join(launched["command"])
    metadata = pid_file.read_text(encoding="utf-8").splitlines()[1]
    assert str(script_path) in metadata
    assert "super-secret-value" not in metadata


def test_windows_terminal_executable_supports_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "_is_windows", lambda: True)
    monkeypatch.setattr(tasks.shutil, "which", lambda name: None)
    launched = {}

    class FakeProcess:
        pid = 9012

    def fake_popen(command):
        launched["command"] = command
        return FakeProcess()

    monkeypatch.setattr(tasks.subprocess, "Popen", fake_popen)

    tasks.launch_command_in_terminal(
        ["codex", "prompt"],
        executable="wt.exe -w 0",
        state_key="custom-wt",
    )

    assert launched["command"][:5] == ["wt.exe", "-w", "0", "new-tab", "--title"]


def test_is_process_running_handles_windows_value_error(monkeypatch):
    def raise_value_error(pid, signal_number):
        raise ValueError("invalid pid")

    monkeypatch.setattr(tasks.os, "kill", raise_value_error)

    assert tasks._is_process_running(1234) is False


def test_is_process_running_handles_windows_system_error(monkeypatch):
    def raise_system_error(pid, signal_number):
        raise SystemError("invalid handle")

    monkeypatch.setattr(tasks.os, "kill", raise_system_error)

    assert tasks._is_process_running(1234) is False


def test_is_process_running_uses_pointer_sized_windows_handle(monkeypatch):
    import ctypes
    from ctypes import wintypes

    class FakeKernelFunction:
        def __init__(self, result):
            self.result = result
            self.calls = []
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            self.calls.append(args)
            return self.result

    class FakeKernel32:
        def __init__(self):
            self.OpenProcess = FakeKernelFunction(0x100000000)
            self.CloseHandle = FakeKernelFunction(True)
            self.GetLastError = FakeKernelFunction(0)

    kernel32 = FakeKernel32()
    monkeypatch.setattr(tasks.os, "name", "nt", raising=False)
    monkeypatch.setattr(ctypes, "windll", type("FakeWindll", (), {"kernel32": kernel32})(), raising=False)

    assert tasks._is_process_running(1234) is True
    assert kernel32.OpenProcess.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert kernel32.OpenProcess.restype is wintypes.HANDLE
    assert kernel32.CloseHandle.calls == [(0x100000000,)]


def test_is_process_running_treats_windows_access_denied_as_running(monkeypatch):
    import ctypes

    class FakeKernelFunction:
        def __init__(self, result):
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self.result

    class FakeKernel32:
        def __init__(self):
            self.OpenProcess = FakeKernelFunction(0)
            self.CloseHandle = FakeKernelFunction(True)
            self.GetLastError = FakeKernelFunction(5)

    monkeypatch.setattr(tasks.os, "name", "nt", raising=False)
    monkeypatch.setattr(ctypes, "windll", type("FakeWindll", (), {"kernel32": FakeKernel32()})(), raising=False)

    assert tasks._is_process_running(1234) is True


def test_terminal_state_dir_falls_back_to_tmp_when_posix_state_home_is_unwritable(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTHEXIS_TERMINAL_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(tasks, "_is_windows", lambda: False)
    monkeypatch.setattr(tasks.os, "access", lambda path, mode: False)
    monkeypatch.setattr(tasks.Path, "home", staticmethod(lambda: tmp_path / "missing-home"))

    assert tasks._terminal_state_dir() == tmp_path / "tmp" / "arthexis-agent-terminals"


def test_launch_terminal_rejects_symlinked_pid_file(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(tasks, "_is_windows", lambda: False)
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL", encoding="utf-8")
    (tmp_path / "None.pid").symlink_to(victim)
    terminal = AgentTerminal(name="symlink-test", launch_command="echo ready")

    class FakeProcess:
        pid = 1234

    monkeypatch.setattr(tasks.subprocess, "Popen", lambda command: FakeProcess())

    with pytest.raises(OSError):
        tasks._launch_terminal(terminal)

    assert victim.read_text(encoding="utf-8") == "ORIGINAL"


def test_launch_terminal_rejects_symlinked_state_dir(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    state_dir = tmp_path / "state-link"
    try:
        state_dir.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setenv("ARTHEXIS_TERMINAL_STATE_DIR", str(state_dir))
    monkeypatch.setattr(tasks, "_is_windows", lambda: False)
    terminal = AgentTerminal(name="symlink-state", launch_command="echo ready")

    class FakeProcess:
        pid = 1234

    monkeypatch.setattr(tasks.subprocess, "Popen", lambda command: FakeProcess())

    with pytest.raises(PermissionError, match="state dir must not be a symlink"):
        tasks._launch_terminal(terminal)


def test_command_metadata_is_unquoted_and_single_line():
    metadata = tasks._command_metadata(
        ["x-terminal-emulator", "-e", "sh", "-lc", "echo ready\nprintf done"]
    )

    assert metadata == "x-terminal-emulator -e sh -lc echo ready printf done"
    assert "\n" not in metadata
    assert "'" not in metadata


def test_admin_owner_fields_remain_editable_for_ownable_validation(db):
    admin = AgentTerminalAdmin(AgentTerminal, AdminSite())
    request = RequestFactory().get("/admin/terminals/agentterminal/")
    request.user = User.objects.create_superuser(username="owner-admin", password="secret")

    readonly_fields = set(admin.get_readonly_fields(request))
    assert {"user", "group", "avatar"}.isdisjoint(readonly_fields)

    form_class = admin.get_form(request)
    assert issubclass(form_class, OwnableAdminForm)
    assert {"user", "group", "avatar"}.issubset(form_class.base_fields)
