from __future__ import annotations

from pathlib import Path


def test_factory_loop_prefers_repo_python(tmp_path, monkeypatch):
    from scripts import factory_loop

    preferred = tmp_path / ".venv312" / "bin" / "python"
    preferred.parent.mkdir(parents=True, exist_ok=True)
    preferred.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.delenv("FACTORY_REFRESH_PYTHON", raising=False)

    assert factory_loop._preferred_python(tmp_path) == str(preferred)


def test_factory_loop_launch_background_process_uses_preferred_python(tmp_path, monkeypatch):
    from scripts import factory_loop

    preferred = tmp_path / ".venv312" / "bin" / "python"
    preferred.parent.mkdir(parents=True, exist_ok=True)
    preferred.write_text("#!/bin/sh\n", encoding="utf-8")

    script_path = tmp_path / "scripts" / "factory_dashboard.py"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text("print('ok')\n", encoding="utf-8")

    captured = {}

    class FakeProc:
        pid = 4321

        def poll(self):
            return None

    def fake_popen(cmd, cwd, stdout, stderr, start_new_session, text):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return FakeProc()

    monkeypatch.setattr(factory_loop.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(factory_loop, "_pid_running", lambda pid_path: False)

    proc = factory_loop._launch_background_process(
        tmp_path,
        script_path=script_path,
        pid_path=tmp_path / "data" / "factory" / "dashboard.pid",
        log_path=tmp_path / "data" / "factory" / "dashboard.log",
        args=["--port", "8787"],
    )

    assert proc is not None
    assert captured["cmd"][0] == str(preferred)
    assert captured["cmd"][1] == str(script_path)
    assert captured["cmd"][2:] == ["--port", "8787"]
