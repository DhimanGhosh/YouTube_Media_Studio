"""Offline tests for the Qt-independent GUI operation adapter."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.runtime.operations import execute_operation
from youtube_audio_video_downloader.domain.models import (
    AudioQuality,
    DownloadResult,
    DownloadStatus,
    MediaSelectionKind,
    VideoJob,
    VideoQuality,
)
from youtube_audio_video_downloader.services.albums.album_metadata_enricher import (
    MetadataEnrichmentReport,
)
from youtube_audio_video_downloader.services.downloads.video_downloader import YouTubeVideoDownloader


class GuiOperationsTest(unittest.TestCase):
    def test_album_summary_completes_parent_when_all_enabled_tracks_finish(self) -> None:
        results = [
            DownloadResult(
                song="Album / Source Track 01",
                status=DownloadStatus.SKIPPED,
                reason="download=false",
            ),
            DownloadResult(
                song="Album / Track 01",
                status=DownloadStatus.DOWNLOADED,
                file_name="one.mp3",
            ),
            DownloadResult(
                song="Album / Track 02",
                status=DownloadStatus.ALREADY_EXISTS,
                file_name="two.mp3",
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "youtube_audio_video_downloader.gui.runtime.operations.YouTubeAlbumSplitter"
        ) as splitter_class:
            splitter_class.return_value.split_from_input.return_value = results
            summary = execute_operation(
                "album",
                {
                    "input_data": {
                        "Album": {
                            "download": "true",
                            "tracks": [
                                {"Disabled": {"download": "false"}},
                                {"First": {"download": "true"}},
                                {"Second": {"download": "true"}},
                            ],
                        }
                    },
                    "output_dir": temporary_directory,
                    "auto_enrich_downloads": False,
                },
                CancellationToken(),
            )

        self.assertIn("Album", summary.completed_items)

    def test_jukebox_summary_completes_parent_when_all_enabled_tracks_finish(
        self,
    ) -> None:
        results = [
            DownloadResult(
                song="Compilation / Source Track 01",
                status=DownloadStatus.SKIPPED,
                reason="download=false",
            ),
            DownloadResult(
                song="Compilation / Track 01",
                status=DownloadStatus.DOWNLOADED,
                file_name="one.mp3",
            ),
            DownloadResult(
                song="Compilation / Track 02",
                status=DownloadStatus.ALREADY_EXISTS,
                file_name="two.mp3",
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory, patch(
            "youtube_audio_video_downloader.gui.runtime.operations.YouTubeJukeboxSplitter"
        ) as splitter_class:
            splitter_class.return_value.split_from_json.return_value = results
            summary = execute_operation(
                "jukebox",
                {
                    "input_data": {
                        "Compilation": {
                            "download": "true",
                            "tracks": [
                                {"Disabled": {"download": "false"}},
                                {"First": {"download": "true"}},
                                {"Second": {"download": "true"}},
                            ],
                        }
                    },
                    "output_dir": temporary_directory,
                    "auto_enrich_downloads": False,
                },
                CancellationToken(),
            )

        self.assertIn("Compilation", summary.completed_items)

    def test_audio_download_automatically_enriches_the_created_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            downloaded = output_dir / "Song.mp3"
            downloaded.write_bytes(b"audio")
            result = DownloadResult(
                song="Song",
                status=DownloadStatus.DOWNLOADED,
                file_name=downloaded.name,
            )
            report = MetadataEnrichmentReport(
                scanned=1,
                updated=(downloaded,),
                skipped=(),
                failed=(),
                completed=(downloaded,),
            )
            with patch(
                "youtube_audio_video_downloader.gui.runtime.operations.YouTubeAudioDownloader"
            ) as downloader_class, patch(
                "youtube_audio_video_downloader.gui.runtime.operations.enrich_media_files",
                return_value=report,
            ) as enrich_mock, patch(
                "youtube_audio_video_downloader.gui.runtime.operations.consolidate_audio_in_place"
            ) as consolidate_mock:
                downloader_class.return_value.download_from_json.return_value = [result]

                summary = execute_operation(
                    "audio",
                    {
                        "input_data": {
                            "Song": {"ytb_link": "https://youtu.be/example"}
                        },
                        "output_dir": str(output_dir),
                        "tracker_path": str(output_dir / "tracker.json"),
                    },
                    CancellationToken(),
                )

            self.assertEqual(enrich_mock.call_args.args[0], [downloaded.resolve()])
            consolidate_mock.assert_called_once_with(
                output_dir.resolve(),
                media_paths=[downloaded.resolve()],
                retries=3,
            )
            self.assertEqual(summary.downloaded, 1)
            self.assertEqual(summary.tagged, 1)
            self.assertEqual(Path(summary.output_path), output_dir.resolve())

    def test_download_summary_reports_the_folder_containing_an_absolute_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            nested_dir = output_dir / "Artist" / "Album"
            nested_dir.mkdir(parents=True)
            downloaded = nested_dir / "Song.mp3"
            downloaded.write_bytes(b"audio")
            result = DownloadResult(
                song="Song",
                status=DownloadStatus.DOWNLOADED,
                file_name=str(downloaded),
            )
            with patch(
                "youtube_audio_video_downloader.gui.runtime.operations.YouTubeAudioDownloader"
            ) as downloader_class:
                downloader_class.return_value.download_from_json.return_value = [result]
                summary = execute_operation(
                    "audio",
                    {
                        "input_data": {
                            "Song": {"ytb_link": "https://youtu.be/example"}
                        },
                        "output_dir": str(output_dir),
                        "auto_enrich_downloads": False,
                    },
                    CancellationToken(),
                )

            self.assertEqual(Path(summary.output_path), nested_dir.resolve())

    def test_download_summary_keeps_existing_parent_when_output_was_moved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            nested_dir = output_dir / "Artist" / "Album"
            nested_dir.mkdir(parents=True)
            result = DownloadResult(
                song="Song",
                status=DownloadStatus.DOWNLOADED,
                file_name=str(Path("Artist") / "Album" / "Song.mp3"),
            )
            with patch(
                "youtube_audio_video_downloader.gui.runtime.operations.YouTubeAudioDownloader"
            ) as downloader_class:
                downloader_class.return_value.download_from_json.return_value = [result]
                summary = execute_operation(
                    "audio",
                    {
                        "input_data": {
                            "Song": {"ytb_link": "https://youtu.be/example"}
                        },
                        "output_dir": str(output_dir),
                        "auto_enrich_downloads": False,
                    },
                    CancellationToken(),
                )

            self.assertEqual(Path(summary.output_path), nested_dir.resolve())

    def test_agentic_model_reaches_every_post_download_enricher(self) -> None:
        """Every downloader must use the same configured metadata adjudicator."""

        workflows = (
            ("audio", "YouTubeAudioDownloader", "download_from_json"),
            ("video", "YouTubeVideoDownloader", "download_from_json"),
            ("album", "YouTubeAlbumSplitter", "split_from_input"),
            ("jukebox", "YouTubeJukeboxSplitter", "split_from_json"),
        )
        for operation, service_name, service_method in workflows:
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                output_dir = Path(directory)
                downloaded = output_dir / f"{operation}.mp3"
                downloaded.write_bytes(b"audio")
                result = DownloadResult(
                    song=f"{operation} song",
                    status=DownloadStatus.DOWNLOADED,
                    file_name=downloaded.name,
                )
                report = MetadataEnrichmentReport(
                    scanned=1,
                    updated=(downloaded,),
                    skipped=(),
                    failed=(),
                    completed=(downloaded,),
                )
                with patch(
                    f"youtube_audio_video_downloader.gui.runtime.operations.{service_name}"
                ) as service_class, patch(
                    "youtube_audio_video_downloader.gui.runtime.operations.enrich_media_files",
                    return_value=report,
                ) as enrich_mock, patch(
                    "youtube_audio_video_downloader.gui.runtime.operations.consolidate_audio_in_place"
                ):
                    getattr(service_class.return_value, service_method).return_value = [result]
                    params = {
                        "input_data": {
                            "Item": {"ytb_link": "https://youtu.be/example"}
                        },
                        "output_dir": str(output_dir),
                        "agentic_model": "metadata-agent:test",
                    }
                    if operation == "video":
                        params["audio_output_dir"] = str(output_dir)

                    execute_operation(operation, params, CancellationToken())

                enrich_mock.assert_called_once()
                self.assertEqual(
                    enrich_mock.call_args.kwargs["agentic_model"],
                    "metadata-agent:test",
                )

    def test_audio_gui_uses_the_selected_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "my songs"
            with patch(
                "youtube_audio_video_downloader.gui.runtime.operations.YouTubeAudioDownloader"
            ) as downloader_class:
                downloader_class.return_value.download_from_json.return_value = []
                execute_operation(
                    "audio",
                    {
                        "input_data": {
                            "Example": {
                                "ytb_link": "https://youtu.be/example",
                                "title": "Example",
                                "album": "Example Album",
                                "artists": "Example Artist",
                            }
                        },
                        "mode": "download",
                        "output_dir": str(output_dir),
                        "min_delay": 0,
                        "max_delay": 0,
                    },
                    CancellationToken(),
                )

            _, call_kwargs = downloader_class.return_value.download_from_json.call_args
            self.assertEqual(call_kwargs["output_dir"], output_dir)
            self.assertFalse(call_kwargs["write_report"])

    def test_selected_song_enrichment_runs_through_the_background_adapter(self) -> None:
        enriched = {
            "url": "https://youtu.be/example",
            "title": "Example Song",
            "album": "Example Album",
            "artists": "Example Artist",
            "release_year": "2020",
            "album_art": "https://example.com/cover.jpg",
        }
        with patch(
            "youtube_audio_video_downloader.gui.runtime.operations.enrich_selected_song",
            return_value=enriched,
        ):
            summary = execute_operation(
                "enrich_song",
                {
                    "url": "https://youtu.be/example",
                    "title": "example song",
                    "album": "example album",
                    "artists": "example artist",
                },
                CancellationToken(),
            )

        self.assertEqual(summary.operation, "enrich_song")
        self.assertEqual(json.loads(summary.output_text), enriched)

    def test_format_artists_operation(self) -> None:
        summary = execute_operation(
            "format_artists",
            {"input_text": "Sonu Nigam & Sunidhi Chauhan"},
            CancellationToken(),
        )
        self.assertEqual(summary.output_text, "Sonu Nigam, Sunidhi Chauhan")

    def test_parse_tracks_operation(self) -> None:
        summary = execute_operation(
            "parse_tracks",
            {
                "input_text": "00:00 - First Song by Artist One\n04:10 - Second Song",
                "end_field": "end",
                "unknown_artists": "Unknown",
                "keep_case": False,
            },
            CancellationToken(),
        )
        payload = json.loads(summary.output_text)
        self.assertEqual(payload["tracks"][0]["First Song"]["end"], "00:04:09")
        self.assertEqual(payload["tracks"][1]["Second Song"]["artists"], "Unknown")

    def test_duplicate_links_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            json_path = Path(temporary_directory) / "songs.json"
            json_path.write_text(
                json.dumps(
                    {
                        "One": {"ytb_link": "https://youtu.be/example"},
                        "Two": {"ytb_link": "https://youtu.be/example"},
                    }
                ),
                encoding="utf-8",
            )
            summary = execute_operation(
                "duplicate_links",
                {"input_path": str(json_path)},
                CancellationToken(),
            )
        self.assertEqual(summary.total, 1)

    def test_video_gui_replaces_interactive_ask_with_selected_quality(self) -> None:
        downloader = YouTubeVideoDownloader(interactive_prompts=False)
        video = VideoJob(
            json_key="Example",
            ytb_link="https://youtu.be/example",
            resolution="ask",
        )
        quality = VideoQuality(
            label="1080p",
            height=1080,
            width=1920,
            fps=30.0,
            video_format_id="137",
            audio_format_id="140",
            video_ext="mp4",
            audio_ext="m4a",
            estimated_size_bytes=1000,
        )
        audio = AudioQuality(
            label="MP3",
            format_id="140",
            source_ext="m4a",
            estimated_size_bytes=100,
            abr=128.0,
        )
        selection = downloader._choose_media_selection(
            video=video,
            qualities=[quality],
            audio_quality=audio,
            cli_resolution="FHD",
            mp3_mode="audio-only",
        )
        self.assertEqual(selection.kind, MediaSelectionKind.VIDEO)
        self.assertEqual(selection.video_quality, quality)


if __name__ == "__main__":
    unittest.main()
