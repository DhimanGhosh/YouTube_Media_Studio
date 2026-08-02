from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "desktop_installer.py"
SPEC = importlib.util.spec_from_file_location("desktop_installer_tool", SCRIPT)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


def test_windows_install_copies_gui_cli_uninstaller_and_registers(monkeypatch, tmp_path) -> None:
    payload = tmp_path / "payload"
    gui = payload / "YouTubeMediaStudio.exe"
    cli = payload / "youtube-media-studio.exe"
    uninstaller = payload / "Uninstall YouTube Media Studio.exe"
    for item in (gui, cli, uninstaller):
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text(item.name, encoding="utf-8")

    destination = tmp_path / "installed"
    registered: list[tuple[Path, Path]] = []
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer, "gui_payload", lambda: gui)
    monkeypatch.setattr(installer, "cli_payload", lambda: cli)
    monkeypatch.setattr(installer, "uninstaller_payload", lambda: uninstaller)
    monkeypatch.setattr(installer, "default_gui_destination", lambda: destination)
    monkeypatch.setattr(installer, "cli_destination", lambda: destination / cli.name)
    monkeypatch.setattr(
        installer, "uninstaller_destination", lambda: destination / uninstaller.name
    )
    monkeypatch.setattr(installer, "_windows_shortcut", lambda _executable: None)
    monkeypatch.setattr(installer, "_windows_path", lambda _enable: None)
    monkeypatch.setattr(
        installer,
        "_windows_register_uninstaller",
        lambda uninstall_path, executable: registered.append((uninstall_path, executable)),
    )

    installed_gui, installed_cli = installer.install(include_cli=True)

    assert installed_gui == destination
    assert installed_cli == destination / cli.name
    assert (destination / gui.name).is_file()
    assert (destination / cli.name).is_file()
    assert (destination / uninstaller.name).is_file()
    assert registered == [(destination / uninstaller.name, destination / gui.name)]


def test_windows_shortcut_uses_encoded_script_and_environment(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "Program Files" / "YouTubeMediaStudio.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    appdata = tmp_path / "AppData" / "Roaming"
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    installer._windows_shortcut(executable)

    command = captured["command"]
    environment = captured["environment"]
    assert "-EncodedCommand" in command
    assert environment["YMS_TARGET_PATH"] == str(executable)
    assert environment["YMS_SHORTCUT_PATH"].endswith("YouTube Media Studio.lnk")
    assert (appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs").is_dir()
