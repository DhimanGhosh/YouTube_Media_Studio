from __future__ import annotations

import importlib.util
import json
import os
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "release.py"
SPEC = importlib.util.spec_from_file_location("release_tool", SCRIPT)
assert SPEC and SPEC.loader
release_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_tool
SPEC.loader.exec_module(release_tool)


def test_every_requested_platform_has_an_explicit_capability() -> None:
    assert set(release_tool.TARGETS) == {"wheel", "windows", "linux", "macos", "raspi"}
    assert release_tool.TARGETS["raspi"].artifact == "CLI installer tar.gz"
    assert release_tool.TARGETS["windows"].artifact == "GUI setup EXE"
    assert release_tool.TARGETS["linux"].artifact == "GUI installer .run"
    assert release_tool.TARGETS["macos"].artifact == "drag-and-drop DMG"


def test_plan_json_is_machine_readable(capsys) -> None:
    release_tool.plan(json_output=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == release_tool.project_version()
    assert len(payload["targets"]) == 5


def test_build_target_listing_prints_every_copyable_target_command(capsys) -> None:
    release_tool.list_build_targets()
    output = capsys.readouterr().out
    assert "Available values for build --target:" in output
    assert "--target current" in output
    for target in release_tool.TARGETS:
        assert f"--target {target}" in output


def test_host_target_is_known() -> None:
    assert release_tool.host_target() in {"windows", "linux", "macos", "unsupported"}


def test_every_desktop_target_has_a_native_icon() -> None:
    assert release_tool.desktop_icon_for_target("windows").suffix == ".ico"
    assert release_tool.desktop_icon_for_target("macos").suffix == ".icns"
    assert release_tool.desktop_icon_for_target("linux").suffix == ".png"


def test_macos_desktop_build_rejects_intel_hosts(monkeypatch) -> None:
    monkeypatch.setattr(release_tool.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(release_tool.platform, "machine", lambda: "x86_64")

    with pytest.raises(SystemExit, match="Intel macOS builds are not supported"):
        release_tool.build_desktop("macos")


def test_clean_removes_only_generated_paths(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(release_tool, "ROOT", tmp_path)
    generated = [
        tmp_path / "build",
        tmp_path / "dist",
        tmp_path / ".pytest_cache",
        tmp_path / "src" / "package" / "__pycache__",
        tmp_path / "src" / "package.egg-info",
    ]
    for path in generated:
        path.mkdir(parents=True)
        (path / "generated.txt").write_text("generated", encoding="utf-8")
    preserved = [tmp_path / ".venv", tmp_path / "config", tmp_path / "crash_reports"]
    for path in preserved:
        path.mkdir()
    (tmp_path / "songs.json").write_text("[]", encoding="utf-8")

    assert release_tool.clean_generated() == len(generated)
    assert all(not path.exists() for path in generated)
    assert all(path.exists() for path in preserved)
    assert (tmp_path / "songs.json").exists()


def test_clean_dry_run_does_not_remove(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(release_tool, "ROOT", tmp_path)
    generated = tmp_path / "dist"
    generated.mkdir()
    assert release_tool.clean_generated(dry_run=True) == 1
    assert generated.exists()


def test_clean_preserves_legacy_windows_settings_before_removing_dist(
    monkeypatch, tmp_path
) -> None:
    dist = tmp_path / "dist"
    legacy = dist / "YouTubeMediaStudioData"
    legacy.mkdir(parents=True)
    (legacy / "settings.ini").write_text("[defaults]\nworkers=6\n", encoding="utf-8")
    appdata = tmp_path / "AppData" / "Roaming"
    monkeypatch.setattr(release_tool, "ROOT", tmp_path)
    monkeypatch.setattr(release_tool, "DIST", dist)
    monkeypatch.setattr(release_tool.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(appdata))

    release_tool.clean_generated()

    preserved = appdata / "DhimanTools" / "YouTube Media Studio" / "settings.ini"
    assert preserved.read_text(encoding="utf-8") == "[defaults]\nworkers=6\n"
    assert not dist.exists()


def test_packaged_doctor_runs_with_sanitized_path(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "YouTubeMediaStudio.exe"
    executable.touch()
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(release_tool.subprocess, "run", fake_run)

    release_tool.verify_packaged_application(executable)

    assert captured["command"] == [str(executable), "doctor"]
    environment = captured["environment"]
    assert ".venv" not in environment["PATH"]


def test_packaged_installer_runs_payload_self_check(monkeypatch, tmp_path) -> None:
    installer = tmp_path / "YouTubeMediaStudio-Setup.exe"
    installer.touch()
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="GUI payload", stderr="")

    monkeypatch.setattr(release_tool.subprocess, "run", fake_run)
    release_tool.verify_packaged_installer(installer)

    assert captured["command"] == [str(installer), "--check"]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows cannot create the macOS Applications symlink without elevation",
)
def test_macos_dmg_contains_app_and_applications_alias(monkeypatch, tmp_path) -> None:
    app = tmp_path / "YouTubeMediaStudio.app"
    executable = app / "Contents" / "MacOS"
    executable.mkdir(parents=True)
    (executable / "YouTubeMediaStudio").write_text("binary", encoding="utf-8")
    artifact = tmp_path / "youtube-media-studio-2.0.0-macos-arm64-installer.dmg"
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd=release_tool.ROOT):
        source_folder = Path(command[command.index("-srcfolder") + 1])
        captured["command"] = command
        captured["cwd"] = cwd
        captured["entries"] = sorted(item.name for item in source_folder.iterdir())
        captured["applications_target"] = (source_folder / "Applications").readlink()

    monkeypatch.setattr(release_tool, "run", fake_run)

    release_tool.create_macos_drag_drop_dmg(app, artifact)

    assert captured["entries"] == ["Applications", "YouTubeMediaStudio.app"]
    assert captured["applications_target"] == Path("/Applications")
    assert captured["command"][-1] == str(artifact)


def test_macos_signing_skips_without_identity(monkeypatch, tmp_path) -> None:
    app = tmp_path / "YouTubeMediaStudio.app"
    app.mkdir()
    calls: list[list[str]] = []

    monkeypatch.delenv("MACOS_CODESIGN_IDENTITY", raising=False)
    monkeypatch.setattr(
        release_tool,
        "run",
        lambda command, *, cwd=release_tool.ROOT: calls.append(command),
    )

    notarized = release_tool.sign_and_notarize_macos_app(app, tmp_path)

    assert notarized is False
    assert calls == []


def test_macos_signing_requires_notarization_credentials(monkeypatch, tmp_path) -> None:
    app = tmp_path / "YouTubeMediaStudio.app"
    app.mkdir()

    monkeypatch.setenv("MACOS_CODESIGN_IDENTITY", "Developer ID Application: Example")
    monkeypatch.delenv("APPLE_ID", raising=False)
    monkeypatch.delenv("APPLE_TEAM_ID", raising=False)
    monkeypatch.delenv("APPLE_APP_SPECIFIC_PASSWORD", raising=False)
    monkeypatch.setattr(
        release_tool,
        "run",
        lambda command, *, cwd=release_tool.ROOT: None,
    )

    with pytest.raises(RuntimeError, match="notarization credentials are missing"):
        release_tool.sign_and_notarize_macos_app(app, tmp_path)


def test_macos_signing_notarizes_app_and_dmg(monkeypatch, tmp_path) -> None:
    app = tmp_path / "YouTubeMediaStudio.app"
    app.mkdir()
    dmg = tmp_path / "youtube-media-studio-2.0.0-macos-arm64-installer.dmg"
    commands: list[list[str]] = []

    monkeypatch.setenv("MACOS_CODESIGN_IDENTITY", "Developer ID Application: Example")
    monkeypatch.setenv("APPLE_ID", "developer@example.com")
    monkeypatch.setenv("APPLE_TEAM_ID", "ABCDE12345")
    monkeypatch.setenv("APPLE_APP_SPECIFIC_PASSWORD", "app-specific-password")
    monkeypatch.setattr(
        release_tool,
        "run",
        lambda command, *, cwd=release_tool.ROOT: commands.append(command),
    )

    assert release_tool.sign_and_notarize_macos_app(app, tmp_path) is True
    release_tool.sign_and_notarize_macos_dmg(dmg)

    command_names = [" ".join(command[:3]) for command in commands]
    assert "codesign --force --deep" in command_names
    assert "ditto -c -k" in command_names
    assert command_names.count("xcrun notarytool submit") == 2
    assert command_names.count("xcrun stapler staple") == 2
    assert command_names.count("xcrun stapler validate") == 2


def test_raspi_bundle_installs_an_uninstall_command(monkeypatch, tmp_path) -> None:
    root = tmp_path / "project"
    dist = root / "dist"
    dist.mkdir(parents=True)
    wheel = dist / "youtube_media_studio-2.0.0-py3-none-any.whl"
    wheel.touch()
    (root / "README.md").write_text("setup", encoding="utf-8")
    monkeypatch.setattr(release_tool, "ROOT", root)
    monkeypatch.setattr(release_tool, "DIST", dist)
    monkeypatch.setattr(release_tool, "project_version", lambda: "2.0.0")
    monkeypatch.setattr(release_tool, "newest_wheel", lambda: wheel)

    archive = release_tool.build_raspi()

    with tarfile.open(archive, "r:gz") as bundle:
        names = bundle.getnames()
        install_text = bundle.extractfile(
            next(name for name in names if name.endswith("install.sh"))
        )
        uninstall_text = bundle.extractfile(
            next(name for name in names if name.endswith("uninstall.sh"))
        )
        assert install_text and uninstall_text
        assert "youtube-media-studio-uninstall" in install_text.read().decode()
        assert "youtube-media-tools" in uninstall_text.read().decode()


def test_python_wheel_uses_installable_v_prefixed_release_name(
    monkeypatch, tmp_path
) -> None:
    root = tmp_path / "project"
    dist = root / "dist"
    canonical = dist / "youtube_media_studio-2.10.0-py3-none-any.whl"

    def fake_run(_command, *, cwd=release_tool.ROOT):
        dist.mkdir(parents=True, exist_ok=True)
        canonical.touch()

    monkeypatch.setattr(release_tool, "ROOT", root)
    monkeypatch.setattr(release_tool, "DIST", dist)
    monkeypatch.setattr(release_tool, "project_version", lambda: "2.10.0")
    monkeypatch.setattr(release_tool, "run", fake_run)

    wheel = release_tool.build_python()

    assert wheel.name == "youtube_media_studio-v2.10.0-py3-none-any.whl"
    assert wheel.is_file()
    assert not canonical.exists()
