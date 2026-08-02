from __future__ import annotations

import json
import os

import pytest
from PyQt6.QtCore import QSettings

from youtube_audio_video_downloader.config.app_storage import (
    DATA_DIRECTORY_ENV,
    DATA_DIRECTORY_NAME,
    LOCATION_FILE_NAME,
    MIGRATION_MARKER_NAME,
    copy_application_data,
    merge_newer_application_data,
    migrate_legacy_data,
    resolve_data_directory,
    save_data_directory_choice,
)
from youtube_audio_video_downloader.config import app_storage


def test_default_data_directory_is_beside_application(tmp_path) -> None:
    selected = resolve_data_directory(base_directory=tmp_path, environ={})

    assert selected == tmp_path / DATA_DIRECTORY_NAME
    assert selected.is_dir()


def test_frozen_windows_default_uses_persistent_appdata(monkeypatch, tmp_path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    executable = tmp_path / "install" / "YouTubeMediaStudio.exe"
    executable.parent.mkdir()
    monkeypatch.setattr(app_storage.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_storage.sys, "platform", "win32")
    monkeypatch.setattr(app_storage.sys, "executable", str(executable))
    monkeypatch.setenv("APPDATA", str(appdata))

    selected = resolve_data_directory(environ={})

    assert selected == appdata / "DhimanTools" / "YouTube Media Studio"


def test_frozen_windows_imports_old_beside_exe_settings(monkeypatch, tmp_path) -> None:
    appdata = tmp_path / "AppData" / "Roaming"
    executable = tmp_path / "install" / "YouTubeMediaStudio.exe"
    legacy = executable.parent / DATA_DIRECTORY_NAME
    legacy.mkdir(parents=True)
    (legacy / "settings.ini").write_text("[defaults]\nworkers=7\n", encoding="utf-8")
    monkeypatch.setattr(app_storage.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_storage.sys, "platform", "win32")
    monkeypatch.setattr(app_storage.sys, "executable", str(executable))
    monkeypatch.setenv("APPDATA", str(appdata))

    selected = resolve_data_directory(environ={})

    assert (selected / "settings.ini").read_text(encoding="utf-8") == (
        "[defaults]\nworkers=7\n"
    )


def test_saved_choice_is_used_on_next_start(tmp_path) -> None:
    selected = tmp_path / "custom-data"
    save_data_directory_choice(selected, base_directory=tmp_path)

    assert resolve_data_directory(base_directory=tmp_path, environ={}) == selected
    payload = json.loads((tmp_path / LOCATION_FILE_NAME).read_text(encoding="utf-8"))
    assert payload["data_directory"] == str(selected)


def test_environment_override_has_highest_priority(tmp_path) -> None:
    saved = tmp_path / "saved"
    override = tmp_path / "override"
    save_data_directory_choice(saved, base_directory=tmp_path)

    selected = resolve_data_directory(
        base_directory=tmp_path,
        environ={DATA_DIRECTORY_ENV: str(override)},
    )

    assert selected == override


def test_copy_preserves_existing_destination_files(tmp_path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    (source / "settings.ini").write_text("source", encoding="utf-8")
    (source / "tracker.json").write_text("tracker", encoding="utf-8")
    (destination / "settings.ini").write_text("destination", encoding="utf-8")

    copied = copy_application_data(source, destination)

    assert (destination / "settings.ini").read_text(encoding="utf-8") == "destination"
    assert (destination / "tracker.json").read_text(encoding="utf-8") == "tracker"
    assert copied == [destination / "tracker.json"]


def test_copy_rejects_destination_inside_source(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(OSError, match="cannot be inside"):
        copy_application_data(source, source / "nested")


def test_copy_to_same_directory_is_a_no_op(tmp_path) -> None:
    assert copy_application_data(tmp_path, tmp_path) == []


def test_legacy_merge_uses_newest_file_without_losing_newer_destination(
    tmp_path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    source_settings = source / "settings.ini"
    destination_settings = destination / "settings.ini"
    source_settings.write_text("new source", encoding="utf-8")
    destination_settings.write_text("old destination", encoding="utf-8")
    os.utime(destination_settings, (1_000_000_000, 1_000_000_000))

    merge_newer_application_data(source, destination)

    assert destination_settings.read_text(encoding="utf-8") == "new source"


def test_old_song_tracker_is_copied_after_an_earlier_storage_migration(tmp_path) -> None:
    legacy = tmp_path / "song_enrichment_tracker.json"
    legacy.write_text('{"files": {"song.mp3": {}}}', encoding="utf-8")
    (tmp_path / MIGRATION_MARKER_NAME).write_text("already migrated", encoding="utf-8")
    settings = QSettings(
        str(tmp_path / "settings.ini"),
        QSettings.Format.IniFormat,
    )

    migrate_legacy_data(tmp_path, settings)

    renamed = tmp_path / "album_enrichment_tracker.json"
    assert renamed.read_text(encoding="utf-8") == legacy.read_text(encoding="utf-8")
