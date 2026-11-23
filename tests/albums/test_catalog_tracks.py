from __future__ import annotations

import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.services.albums.catalog_tracks import find_catalog_album_tracks


class CatalogTracksTest(unittest.TestCase):
    @patch("youtube_audio_video_downloader.services.albums.catalog_tracks._request")
    def test_exact_ep_uses_collection_identity_year_and_track_order(self, request) -> None:
        album = {"collectionId": 123, "collectionName": "Dilfek - EP",
                 "releaseDate": "2026-10-16", "trackCount": 2}
        def song(number, title, collection=123):
            return {"wrapperType": "track", "kind": "song", "collectionId": collection,
                    "discNumber": 1, "trackNumber": number, "trackName": title,
                    "artistName": "Rito Riba & Saaheal", "trackTimeMillis": 179627}
        request.side_effect = [
            [album, {**album, "collectionId": 456, "releaseDate": "2025-10-16"}],
            [album, song(2, "Joganiya"), song(1, "Makhmali"), song(3, "Other", 456)],
        ]
        tracks = find_catalog_album_tracks("Dilfek", "2026")
        self.assertEqual([t["title"] for t in tracks], ["Makhmali", "Joganiya"])
        self.assertEqual(tracks[0]["artists"], "Rito Riba, Saaheal")
        self.assertEqual(tracks[0]["duration_seconds"], 180)
        self.assertEqual(request.call_args.kwargs["id"], 123)

    @patch("youtube_audio_video_downloader.services.albums.catalog_tracks._request")
    def test_rejects_ambiguous_wrong_year_and_incomplete_albums(self, request) -> None:
        album = {"collectionId": 123, "collectionName": "Dilfek - EP",
                 "releaseDate": "2026-10-16", "trackCount": 5}
        for candidates in (
            [{**album, "collectionName": "Dilfek Remixes"}],
            [{**album, "releaseDate": "2025-10-16"}],
            [album, {**album, "collectionId": 456}],
        ):
            with self.subTest(candidates=candidates):
                request.reset_mock()
                request.side_effect = [candidates]
                self.assertEqual(find_catalog_album_tracks("Dilfek", "2026"), [])
                self.assertEqual(request.call_count, 1)
        request.side_effect = [[album], []]
        self.assertEqual(find_catalog_album_tracks("Dilfek", "2026"), [])
