from unittest.mock import Mock

from scripts import launch_offline as launcher


def test_repeat_launch_opens_existing_without_starting_process(monkeypatch):
    monkeypatch.setattr(launcher, "port_in_use", lambda: True)
    monkeypatch.setattr(launcher, "is_our_app", lambda: True)
    monkeypatch.setattr(launcher, "healthy", lambda: True)
    opener = Mock(return_value=True)
    start = Mock()
    upgrade = Mock()
    monkeypatch.setattr(launcher.webbrowser, "open", opener)
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    monkeypatch.setattr(launcher.subprocess, "run", upgrade)
    assert launcher.main() == 0
    opener.assert_called_once_with(launcher.URL, new=2)
    start.assert_not_called()
    upgrade.assert_not_called()


def test_foreign_port_does_not_open_or_stop_other_service(monkeypatch):
    monkeypatch.setattr(launcher, "port_in_use", lambda: True)
    monkeypatch.setattr(launcher, "is_our_app", lambda: False)
    opener = Mock()
    start = Mock()
    monkeypatch.setattr(launcher.webbrowser, "open", opener)
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    assert launcher.main() == 1
    opener.assert_not_called()
    start.assert_not_called()


def test_new_launch_opens_after_ready_and_stays_offline(monkeypatch):
    monkeypatch.setattr(launcher, "port_in_use", lambda: False)
    health = iter([False, True])
    monkeypatch.setattr(launcher, "healthy", lambda: next(health))
    monkeypatch.setattr(launcher.time, "sleep", lambda _: None)
    child = Mock()
    child.poll.return_value = None
    child.wait.return_value = 0
    start = Mock(return_value=child)
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    monkeypatch.setattr(launcher.subprocess, "run", Mock())
    opener = Mock(return_value=True)
    monkeypatch.setattr(launcher.webbrowser, "open", opener)
    assert launcher.main() == 0
    assert start.call_args.kwargs["env"]["ANTHROPIC_API_KEY"] == ""
    assert start.call_args.kwargs["cwd"] == launcher.PROJECT
    opener.assert_called_once()
    child.wait.assert_called_once()


def test_app_identity_checks_project_directory(monkeypatch):
    outputs = iter(["123\n", f"p123\nfcwd\nn{launcher.PROJECT}\n"])
    monkeypatch.setattr(launcher.subprocess, "check_output", lambda *a, **k: next(outputs))
    assert launcher.is_our_app()
    outputs = iter(["123\n", "p123\nfcwd\nn/another/project\n"])
    assert not launcher.is_our_app()
