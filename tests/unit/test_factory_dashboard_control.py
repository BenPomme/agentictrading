from __future__ import annotations


def test_apply_control_action_start_clears_pause_and_launches_loop(tmp_path, monkeypatch):
    from scripts import factory_dashboard as dash

    monkeypatch.setattr(dash, "project_root", tmp_path)
    pause_flag = tmp_path / "data" / "factory" / "factory_paused.flag"
    pause_flag.parent.mkdir(parents=True, exist_ok=True)
    pause_flag.write_text("paused", encoding="utf-8")

    launched = {"called": False}

    def fake_launch() -> bool:
        launched["called"] = True
        return True

    monkeypatch.setattr(dash, "_launch_factory_loop", fake_launch)
    monkeypatch.setattr(
        dash,
        "_control_state",
        lambda: {
            "factory_paused": False,
            "factory_running": True,
            "refresh_scheduler_running": True,
            "dashboard_running": True,
            "system_running": True,
        },
    )

    state = dash._apply_control_action("start")

    assert launched["called"] is True
    assert pause_flag.exists() is False
    assert state["factory_running"] is True
    assert state["started_factory_loop"] is True


def test_apply_control_action_stop_sets_pause_and_terminates_loop_and_scheduler(tmp_path, monkeypatch):
    from scripts import factory_dashboard as dash

    monkeypatch.setattr(dash, "project_root", tmp_path)
    terminated = []
    monkeypatch.setattr(dash, "_terminate_pid", lambda path: terminated.append(path.name) or True)
    monkeypatch.setattr(
        dash,
        "_control_state",
        lambda: {
            "factory_paused": True,
            "factory_running": False,
            "refresh_scheduler_running": False,
            "dashboard_running": True,
            "system_running": False,
        },
    )

    state = dash._apply_control_action("stop")

    assert (tmp_path / "data" / "factory" / "factory_paused.flag").exists()
    assert "factory_loop.pid" in terminated
    assert "data_refresh_scheduler.pid" in terminated
    assert state["factory_running"] is False


def test_with_control_state_augments_snapshot(monkeypatch):
    from scripts import factory_dashboard as dash

    monkeypatch.setattr(
        dash,
        "_control_state",
        lambda: {
            "factory_paused": False,
            "factory_running": True,
            "refresh_scheduler_running": True,
            "dashboard_running": True,
            "system_running": True,
        },
    )

    payload = dash._with_control_state({"factory": {"status": "running"}})

    assert payload["factory_running"] is True
    assert payload["refresh_scheduler_running"] is True
    assert payload["factory"]["status"] == "running"
