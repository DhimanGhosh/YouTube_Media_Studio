from __future__ import annotations

import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch

from youtube_audio_video_downloader.services.albums.wikipedia_tracks import (
    extract_track_artists_from_html,
    extract_tracks_from_html,
    clean_wikipedia_track_title,
    find_wikipedia_song_metadata,
    _api,
)


class WikipediaTracksTest(unittest.TestCase):
    def test_extracts_title_and_singers_from_soundtrack_table(self) -> None:
        html = """<table><tr><th>No.</th><th>Title</th><th>Singer(s)</th></tr>
        <tr><td>1</td><td>Tu Jo Mila (Dekhna Na Mudke)</td><td>Javed Ali</td></tr></table>"""
        self.assertEqual(
            extract_track_artists_from_html(html),
            {"tu jo mila dekhna na mudke": "Javed Ali"},
        )
        tracks = extract_tracks_from_html(
            "<table><tr><th>Title</th><th>Singer(s)</th><th>Length</th></tr>"
            "<tr><td>Radio</td><td>Kamaal Khan</td><td>4:39</td></tr></table>"
        )
        self.assertEqual(tracks[0]["duration_seconds"], 279)
        artist_header = extract_tracks_from_html(
            "<table><tr><th>Title</th><th>Artist(s)</th><th>Length</th></tr>"
            "<tr><td>Johnny Johnny</td><td>Jigar Saraiya</td><td>3:38</td></tr></table>"
        )
        self.assertEqual(artist_header[0]["artists"], "Jigar Saraiya")

    def test_normalizes_wikipedia_artist_separators(self) -> None:
        tracks = extract_tracks_from_html(
            "<table><tr><th>Title</th><th>Artist(s)</th></tr>"
            "<tr><td>Tera Naam Doon</td>"
            "<td>Atif Aslam &amp; Shalmali Kholgade</td></tr></table>"
        )
        self.assertEqual(tracks[0]["artists"], "Atif Aslam, Shalmali Kholgade")

    def test_removes_featured_artist_but_keeps_version_labels(self) -> None:
        self.assertEqual(
            clean_wikipedia_track_title(
                "Tinka Tinka Dil Mera (ft. Jubin Nautiyal)", "Jubin Nautiyal"
            ),
            "Tinka Tinka Dil Mera",
        )
        self.assertEqual(
            clean_wikipedia_track_title("Kuch Nahi (Reprise)", "Shafqat Amanat Ali"),
            "Kuch Nahi (Reprise)",
        )
        self.assertEqual(
            clean_wikipedia_track_title("Mere Sawaal Ka (King Version)", "King"),
            "Mere Sawaal Ka (King Version)",
        )
        self.assertEqual(
            clean_wikipedia_track_title("Tu Jo Mila (Dekhna Na Mudke)", "Javed Ali"),
            "Tu Jo Mila (Dekhna Na Mudke)",
        )

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_song_lookup_requires_an_exact_wikipedia_table_row(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "Example Album (soundtrack)"}]}},
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Title</th><th>Artist(s)</th></tr>"
                        "<tr><td>Exact Song</td><td>Exact Artist</td></tr></table>"
                    )
                }
            },
        ]
        result = find_wikipedia_song_metadata("Exact Song", "Exact Song")
        self.assertEqual(result["album"], "Example Album")
        self.assertEqual(result["artists"], "Exact Artist")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_soundtrack_lookup_requires_every_multi_artist_hint(self, api_mock) -> None:
        api_mock.side_effect = [
            {
                "query": {
                    "search": [
                        {"title": "Raaz 3 (soundtrack)"},
                        {"title": "Amanush (2010 film)"},
                    ]
                }
            },
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Title</th><th>Singer(s)</th></tr>"
                        "<tr><td>Oh My Love</td>"
                        "<td>Sonu Nigam &amp; Shreya Ghoshal</td></tr></table>"
                    )
                }
            },
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Title</th><th>Singer(s)</th></tr>"
                        "<tr><td>Oh My Love</td>"
                        "<td>Kunal Ganjawala &amp; Shreya Ghoshal</td></tr></table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Oh My Love", "Oh My Love", "Kunal Ganjawala, Shreya Ghoshal"
        )

        self.assertEqual(result["album"], "Amanush")
        self.assertEqual(result["artists"], "Kunal Ganjawala, Shreya Ghoshal")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_song_lookup_reads_exact_discography_row_and_year(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "List of songs recorded by Arijit Singh"}]}},
            {
                "parse": {
                    "text": (
                        '<h3 id="2015">2015</h3>'
                        "<table><tr><th>Film</th><th>No</th><th>Song</th>"
                        "<th>Composer(s)</th><th>Co-Singer(s)</th></tr>"
                        "<tr><th>Asche Bochor Abar Hobe</th><td>53</td>"
                        '<td>"Chine Phelechhi Rastaghat"</td>'
                        "<td>Indraadip Das Gupta</td><td>Anweshaa</td></tr></table>"
                    )
                }
            },
        ]
        result = find_wikipedia_song_metadata(
            "Chine Phelechhi Rastaghat - Arijit Singh, Anweshaa",
            "Chine Phelechhi Rastaghat",
        )
        self.assertEqual(result["album"], "Asche Bochor Abar Hobe")
        self.assertEqual(result["year"], "2015")
        self.assertEqual(result["artists"], "Arijit Singh, Anweshaa")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_discography_lookup_requires_every_multi_artist_hint(self, api_mock) -> None:
        api_mock.side_effect = [
            {
                "query": {
                    "search": [
                        {"title": "Sonu Nigam discography"},
                        {"title": "Kunal Ganjawala discography"},
                    ]
                }
            },
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Year</th><th>Film</th><th>Song</th>"
                        "<th>Co-Singer(s)</th></tr><tr><td>2012</td><td>Raaz 3</td>"
                        "<td>Oh My Love</td><td>Shreya Ghoshal</td></tr></table>"
                    )
                }
            },
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Year</th><th>Film</th><th>Song</th>"
                        "<th>Co-Singer(s)</th></tr><tr><td>2010</td><td>Amanush</td>"
                        "<td>Oh My Love</td><td>Shreya Ghoshal</td></tr></table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Oh My Love", "Oh My Love", "Kunal Ganjawala, Shreya Ghoshal"
        )

        self.assertEqual(result["album"], "Amanush")
        self.assertEqual(result["year"], "2010")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_title_track_label_matches_album_title_soundtrack_row(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "Shedin Dekha Hoyechilo"}]}},
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Title</th><th>Singer(s)</th></tr>"
                        "<tr><td>Shedin Dekha Hoyechilo</td>"
                        "<td>Kunal Ganjawala</td></tr></table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Shedin Dekha Hoyechilo Title Track - Kunal Ganjawala",
            "Shedin Dekha Hoyechilo Title Track",
            "Kunal Ganjawala",
        )

        self.assertEqual(result["album"], "Shedin Dekha Hoyechilo")
        self.assertEqual(result["title"], "Shedin Dekha Hoyechilo")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_title_track_label_matches_discography_title_abbreviation(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "Kunal Ganjawala"}]}},
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Year</th><th>Film</th><th>Song</th></tr>"
                        "<tr><td>2010</td><td>Le Chakka</td>"
                        "<td>Le Chakka Title</td></tr></table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Le Chakka Title Track - Kunal Ganjawala",
            "Le Chakka Title Track",
            "Kunal Ganjawala",
        )

        self.assertEqual(result["album"], "Le Chakka")
        self.assertEqual(result["year"], "2010")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_discography_row_preserves_language_identity(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "List of songs recorded by Arijit Singh"}]}},
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Year</th><th>Language</th><th>Film</th>"
                        "<th>Song</th></tr><tr><td>2014</td><td>Bengali</td>"
                        "<td>Highway</td><td>Khela Sesh</td></tr></table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Khela Sesh", "Khela Sesh", "Arijit Singh"
        )

        self.assertEqual(result["album"], "Highway")
        self.assertEqual(result["language"], "Bengali")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_discography_rowspans_preserve_film_and_year(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "Sonu Nigam discography"}]}},
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Year</th><th>Film</th><th>Song</th>"
                        "<th>Co-artist(s)</th></tr>"
                        '<tr><td rowspan="2">2006</td><td rowspan="2">Hero</td>'
                        '<td>Other Song</td><td></td></tr>'
                        '<tr><td>Bhalo Lage Swapnoke</td><td>Shreya Ghoshal</td></tr>'
                        "</table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Bhalo Lage Swapnoke", "Bhalo Lage Swapnoke", "Sonu Nigam"
        )

        self.assertEqual(result["album"], "Hero")
        self.assertEqual(result["year"], "2006")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._api")
    def test_bengali_chh_romanization_variant_matches_exact_row(self, api_mock) -> None:
        api_mock.side_effect = [
            {"query": {"search": []}},
            {"query": {"search": [{"title": "Sonu Nigam discography"}]}},
            {
                "parse": {
                    "text": (
                        "<table><tr><th>Year</th><th>Film</th><th>Song</th></tr>"
                        "<tr><td>2004</td><td>Bandhan</td>"
                        "<td>Kichu Hashi Kichu Asha</td></tr></table>"
                    )
                }
            },
        ]

        result = find_wikipedia_song_metadata(
            "Kichhu Hashi Kichhu Asha - Lofi",
            "Kichhu Hashi Kichhu Asha",
            "Sonu Nigam",
        )

        self.assertEqual(result["album"], "Bandhan")
        self.assertEqual(result["year"], "2004")

    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks.time.sleep")
    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks._NEXT_API_REQUEST", 0.0)
    @patch("youtube_audio_video_downloader.services.albums.wikipedia_tracks.urlopen")
    def test_wikipedia_api_retries_http_429(self, urlopen_mock, _sleep_mock) -> None:
        urlopen_mock.side_effect = [
            HTTPError("https://en.wikipedia.org", 429, "Too Many Requests", {}, None),
            BytesIO(b'{"query":{"search":[]}}'),
        ]
        result = _api({"action": "query", "format": "json"})
        self.assertEqual(result, {"query": {"search": []}})
        self.assertEqual(urlopen_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
