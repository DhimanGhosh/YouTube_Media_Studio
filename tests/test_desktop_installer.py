from __future__ import annotations

import importlib.util
import os
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "desktop_installer.py"
SPEC = importlib.util.spec_from_file_location("desktop_installer_tool", SCRIPT)
assert SPEC and SPEC.loader
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


def test_archived_payload_is_extracted_with_executable_permissions(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source" / "payload"
    executable = source / "cli" / "youtube-media-studio"
    executable.parent.mkdir(parents=True)
    executable.write_text("binary", encoding="utf-8")
    executable.chmod(0o755)
    frozen_root = tmp_path / "frozen"
    frozen_root.mkdir()
    with tarfile.open(frozen_root / "payload.tar.gz", "w:gz") as bundle:
        bundle.add(source, arcname="payload")

    monkeypatch.setattr(installer.sys, "_MEIPASS", str(frozen_root), raising=False)
    monkeypatch.setattr(installer, "_EXTRACTED_PAYLOAD_ROOT", None)

    extracted = installer.payload_root()

    extracted_executable = extracted / "cli" / "youtube-media-studio"
    assert extracted_executable.read_text(encoding="utf-8") == "binary"
    if os.name != "nt":
        assert extracted_executable.stat().st_mode & 0o111


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
    monkeypatch.setattr(installer, "existing_installation_destination", lambda: None)
    monkeypatch.setattr(installer, "cli_destination", lambda: destination / cli.name)
    monkeypatch.setattr(
        installer, "uninstaller_destination", lambda: destination / uninstaller.name
    )
    monkeypatch.setattr(installer, "_windows_shortcut", lambda _executable: None)
    monkeypatch.setattr(installer, "_windows_path", lambda _enable: None)
    stopped: list[Path] = []
    monkeypatch.setattr(
        installer, "stop_running_application", lambda path: stopped.append(path) or ()
    )
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
    assert stopped == [destination]


def test_windows_install_retries_after_locked_executable(monkeypatch, tmp_path) -> None:
    gui = tmp_path / "payload" / "YouTubeMediaStudio.exe"
    uninstaller = tmp_path / "payload" / "Uninstall YouTube Media Studio.exe"
    gui.parent.mkdir()
    gui.write_text("new gui", encoding="utf-8")
    uninstaller.write_text("uninstaller", encoding="utf-8")
    destination = tmp_path / "installed"
    destination.mkdir()
    (destination / gui.name).write_text("old gui", encoding="utf-8")
    calls: list[Path] = []
    real_replace = installer._replace_path

    def replace_once_locked(source: Path, target: Path) -> None:
        if target.name == gui.name and calls.count(destination) == 1:
            raise PermissionError("locked")
        real_replace(source, target)

    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer, "gui_payload", lambda: gui)
    monkeypatch.setattr(installer, "cli_payload", lambda: tmp_path / "unused-cli")
    monkeypatch.setattr(installer, "uninstaller_payload", lambda: uninstaller)
    monkeypatch.setattr(
        installer, "stop_running_application", lambda path: calls.append(path) or ()
    )
    monkeypatch.setattr(installer, "_replace_path", replace_once_locked)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(installer, "_windows_path", lambda *_args: None)
    monkeypatch.setattr(installer, "_windows_unregister", lambda: None)
    monkeypatch.setattr(installer, "_windows_shortcut", lambda _path: None)
    monkeypatch.setattr(installer, "_windows_register_uninstaller", lambda *_args: None)

    installed_gui, installed_cli = installer.install(False, destination)

    assert installed_gui == destination
    assert installed_cli is None
    assert calls == [destination, destination]
    assert (destination / gui.name).read_text(encoding="utf-8") == "new gui"


def test_upgrade_removes_old_install_but_preserves_application_data(monkeypatch, tmp_path) -> None:
    gui = tmp_path / "payload" / "YouTubeMediaStudio.exe"
    uninstaller = tmp_path / "payload" / "Uninstall YouTube Media Studio.exe"
    gui.parent.mkdir()
    gui.write_text("new gui", encoding="utf-8")
    uninstaller.write_text("new uninstaller", encoding="utf-8")
    destination = tmp_path / "installed"
    destination.mkdir()
    (destination / gui.name).write_text("old gui", encoding="utf-8")
    (destination / "obsolete.dll").write_text("old", encoding="utf-8")
    app_data = tmp_path / "data"
    app_data.mkdir()
    (app_data / "settings.json").write_text("keep", encoding="utf-8")

    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer, "gui_payload", lambda: gui)
    monkeypatch.setattr(installer, "cli_payload", lambda: tmp_path / "unused-cli")
    monkeypatch.setattr(installer, "uninstaller_payload", lambda: uninstaller)
    monkeypatch.setattr(installer, "application_data_directory", lambda: app_data)
    monkeypatch.setattr(installer, "stop_running_application", lambda _path: (99,))
    monkeypatch.setattr(installer, "_windows_path", lambda *_args: None)
    monkeypatch.setattr(installer, "_windows_unregister", lambda: None)
    monkeypatch.setattr(installer, "_windows_shortcut", lambda _path: None)
    monkeypatch.setattr(installer, "_windows_register_uninstaller", lambda *_args: None)

    installer.install(False, destination)

    assert not (destination / "obsolete.dll").exists()
    assert (destination / gui.name).read_text(encoding="utf-8") == "new gui"
    assert (app_data / "settings.json").read_text(encoding="utf-8") == "keep"


def test_windows_process_stop_uses_exact_target_path(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "YouTube Media Studio" / "YouTubeMediaStudio.exe"
    executable.parent.mkdir()
    executable.touch()
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SimpleNamespace(returncode=0, stdout="42\n", stderr="")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)

    stopped = installer._stop_windows_application(executable)

    assert stopped == (42,)
    assert captured["environment"]["YMS_TARGET_PATH"] == str(executable.resolve())
    assert captured["environment"]["YMS_TARGET_NAME"] == executable.name
    assert "-EncodedCommand" in captured["command"]


def test_existing_installation_opens_maintenance_choices(monkeypatch, tmp_path) -> None:
    destination = tmp_path / "installed"
    destination.mkdir()
    (destination / "YouTubeMediaStudio.exe").touch()
    monkeypatch.setattr(installer, "existing_installation_destination", lambda: destination)
    monkeypatch.setattr(installer, "existing_installation_version", lambda: "2.0.4")
    monkeypatch.setattr(installer, "app_version", lambda: "2.0.5")

    app = installer.QApplication.instance() or installer.QApplication([])
    window = installer.InstallerWindow()

    assert window.upgrade_option.isChecked()
    assert "2.0.5" in window.upgrade_option.text()
    assert not window.remove_data_option.isEnabled()
    window.uninstall_option.setChecked(True)
    assert window.remove_data_option.isEnabled()
    window.go_next()
    assert window.pages.currentIndex() == 3
    assert window.next_button.text() == "Uninstall"
    assert "application data will be kept" in window.ready_summary.text()
    window.close()
    app.processEvents()


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
