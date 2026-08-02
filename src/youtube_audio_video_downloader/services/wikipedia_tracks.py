"""Read album track and singer metadata from Wikipedia tables."""

from __future__ import annotations

import json
import re
import threading
import time
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from youtube_audio_video_downloader.config.app_identity import http_user_agent
from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names
from youtube_audio_video_downloader.services.album_names import normalize_album_name


_API_LOCK = threading.Lock()
_NEXT_API_REQUEST = 0.0
_MIN_API_INTERVAL = 0.35


def _api(params: dict[str, str], timeout: float = 12.0) -> dict:
    """Call Wikipedia with staggered starts and bounded 429/maxlag retries."""

    global _NEXT_API_REQUEST
    url = "https://en.wikipedia.org/w/api.php?" + urlencode(params)
    request = Request(url, headers={"User-Agent": http_user_agent()})
    for attempt in range(4):
        # Reserve only the next start time under the lock. The HTTP operation must
        # remain outside it so metadata workers can genuinely overlap their I/O.
        with _API_LOCK:
            now = time.monotonic()
            request_at = max(now, _NEXT_API_REQUEST)
            _NEXT_API_REQUEST = request_at + _MIN_API_INTERVAL
        wait = request_at - now
        if wait > 0:
            time.sleep(wait)
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in {429, 503} or attempt == 3:
                raise
            retry_after = str(exc.headers.get("Retry-After", "") or "").strip()
            retry_delay = float(retry_after) if retry_after.isdigit() else 1.5 * (2 ** attempt)
            retry_delay = min(max(retry_delay, _MIN_API_INTERVAL), 12.0)
            with _API_LOCK:
                _NEXT_API_REQUEST = max(
                    _NEXT_API_REQUEST, time.monotonic() + retry_delay
                )
            time.sleep(retry_delay)
    raise RuntimeError("Wikipedia API retry loop ended unexpectedly")


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list[str] | None = None
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._rowspans: dict[int, tuple[int, str]] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "table" and self._table is None:
            self._table = []
            self._rowspans = {}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
            attributes = dict(attrs)
            try:
                self._cell_rowspan = max(1, int(attributes.get("rowspan", "1")))
            except (TypeError, ValueError):
                self._cell_rowspan = 1
            try:
                self._cell_colspan = max(1, int(attributes.get("colspan", "1")))
            except (TypeError, ValueError):
                self._cell_colspan = 1
        elif tag == "br" and self._cell is not None:
            self._cell.append(", ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append(
                (
                    " ".join("".join(self._cell).split()),
                    self._cell_rowspan,
                    self._cell_colspan,
                )
            )
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row or self._rowspans:
                values: list[str] = []
                new_spans: dict[int, tuple[int, str]] = {}
                column = 0
                for value, rowspan, colspan in self._row:
                    while column in self._rowspans:
                        values.append(self._rowspans[column][1])
                        column += 1
                    for _ in range(colspan):
                        values.append(value)
                        if rowspan > 1:
                            new_spans[column] = (rowspan - 1, value)
                        column += 1
                if self._rowspans:
                    last_column = max(self._rowspans)
                    while column <= last_column:
                        values.append(self._rowspans.get(column, (0, ""))[1])
                        column += 1
                if values:
                    self._table.append(values)
                remaining_spans = {
                    column: (remaining - 1, value)
                    for column, (remaining, value) in self._rowspans.items()
                    if remaining > 1
                }
                remaining_spans.update(new_spans)
                self._rowspans = remaining_spans
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _song_title_key(value: str) -> str:
    """Normalize a narrow, common Bengali Romanization spelling variant."""

    return re.sub(r"chh(?=[aeiou])", "ch", _key(value))


def _title_track_variants(value: str) -> tuple[str, ...]:
    """Return exact-equivalent labels commonly used for a film title song."""

    text = " ".join(str(value or "").split()).strip()
    variants = [text] if text else []
    marker = re.search(
        r"\s*(?:[-–—:]\s*)?(?:\(\s*)?title\s+(?:track|song)(?:\s*\))?\s*$",
        text,
        flags=re.I,
    )
    if marker:
        album_title = text[:marker.start()].strip(" -–—:()")
        if album_title:
            variants.extend((f"{album_title} Title", album_title))
    return tuple(dict.fromkeys(variant for variant in variants if variant))


def _title_matches(requested: str, candidate: str) -> bool:
    wanted = _key(requested).split()
    available = _key(candidate).split()
    return bool(wanted) and any(
        available[index:index + len(wanted)] == wanted
        for index in range(len(available) - len(wanted) + 1)
    )


def clean_wikipedia_track_title(title: str, artists: str) -> str:
    """Remove trailing featured-artist credits already represented in Artists."""
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", title.strip())
    if not match:
        return title.strip()
    base, parenthetical = match.group(1).strip(), match.group(2).strip()
    credit = re.sub(r"^(?:ft|feat|featuring)\.?\s+", "", parenthetical, flags=re.I)
    explicitly_featured = credit != parenthetical
    version_words = {
        "version", "reprise", "remix", "mix", "encore", "recreated",
        "unplugged", "acoustic", "film", "male", "female",
    }
    if set(_key(parenthetical).split()) & version_words and not explicitly_featured:
        return title.strip()
    credit_key = _key(credit)
    artists_key = _key(artists)
    matches_artist = bool(credit_key) and (
        credit_key in artists_key or artists_key in credit_key
    )
    return base if explicitly_featured or matches_artist else title.strip()


def extract_track_artists_from_html(html: str) -> dict[str, str]:
    """Return normalized track-title to singer mappings from Wikipedia tables."""
    return {_key(track["title"]): track["artists"] for track in extract_tracks_from_html(html)}


def extract_tracks_from_html(html: str) -> list[dict[str, object]]:
    """Return ordered title, artists, and duration metadata from soundtrack tables."""
    parser = _TableParser()
    parser.feed(html)
    found: list[dict[str, object]] = []
    for table in parser.tables:
        if not table:
            continue
        headers = [_key(cell) for cell in table[0]]
        title_index = next((i for i, h in enumerate(headers) if h in {"title", "song", "track"}), -1)
        artist_index = next(
            (i for i, h in enumerate(headers) if "singer" in h or "artist" in h or h == "vocals"),
            -1,
        )
        length_index = next((i for i, h in enumerate(headers) if h in {"length", "duration"}), -1)
        if title_index < 0 or artist_index < 0:
            continue
        for row in table[1:]:
            if max(title_index, artist_index) >= len(row):
                continue
            title = re.sub(r'"\s*(?=\()', " ", row[title_index]).strip().strip('"')
            artists = format_artist_names(row[artist_index].strip())
            if title and artists:
                title = clean_wikipedia_track_title(title, artists)
                length = row[length_index].strip() if 0 <= length_index < len(row) else ""
                duration_seconds = 0
                if re.fullmatch(r"\d{1,2}:\d{2}", length):
                    minutes, seconds = length.split(":")
                    duration_seconds = int(minutes) * 60 + int(seconds)
                found.append({
                    "title": title,
                    "artists": artists,
                    "length": length,
                    "duration_seconds": duration_seconds,
                })
    return found


def find_wikipedia_track_artists(album_name: str, release_year: str = "") -> dict[str, str]:
    """Find the most relevant Wikipedia page containing a soundtrack table."""
    album_name = normalize_album_name(album_name)
    query = f"{album_name} {release_year.strip()} soundtrack".strip()
    if not album_name.strip():
        return {}
    search = _api({"action": "query", "list": "search", "srsearch": query, "srlimit": "5", "format": "json"})
    for item in search.get("query", {}).get("search", []):
        title = str(item.get("title") or "")
        context = f"{title} {re.sub(r'<[^>]+>', ' ', str(item.get('snippet') or ''))}"
        if not _title_matches(album_name, title) or (release_year and release_year not in context):
            continue
        parsed = _api({"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": "2"})
        tracks = extract_track_artists_from_html(str(parsed.get("parse", {}).get("text") or ""))
        if tracks:
            return tracks
    return {}


def find_wikipedia_tracks(album_name: str, release_year: str = "") -> list[dict[str, object]]:
    """Find an ordered Wikipedia soundtrack table including track lengths."""
    album_name = normalize_album_name(album_name)
    query = f"{album_name} {release_year.strip()} soundtrack".strip()
    if not album_name.strip():
        return []
    search = _api({"action": "query", "list": "search", "srsearch": query, "srlimit": "5", "format": "json"})
    for item in search.get("query", {}).get("search", []):
        title = str(item.get("title") or "")
        context = f"{title} {re.sub(r'<[^>]+>', ' ', str(item.get('snippet') or ''))}"
        if not _title_matches(album_name, title) or (release_year and release_year not in context):
            continue
        parsed = _api({"action": "parse", "page": title, "prop": "text", "format": "json", "formatversion": "2"})
        tracks = extract_tracks_from_html(str(parsed.get("parse", {}).get("text") or ""))
        if tracks:
            return tracks
    return []


def find_wikipedia_song_metadata(
    file_name_text: str,
    title_hint: str,
    artists_hint: str = "",
) -> dict[str, str]:
    """Resolve a song only from an exact Wikipedia soundtrack/discography row."""

    title_variants = _title_track_variants(title_hint)
    wanted_titles = {_song_title_key(value) for value in title_variants}
    wanted_titles.discard("")
    if not wanted_titles:
        return {}
    artist_text = format_artist_names(artists_hint)
    query_titles = tuple(dict.fromkeys(
        variant
        for title in title_variants
        for variant in (
            title,
            re.sub(r"chh(?=[aeiou])", "ch", title, flags=re.I),
        )
    ))
    focused_queries = tuple(
        " ".join(part for part in (f'"{query_title}"', artist_text, "song") if part)
        for query_title in query_titles if query_title
    )
    broad_query = f"{str(file_name_text or '').strip()} song soundtrack".strip()
    seen_pages: set[str] = set()
    for query in dict.fromkeys((*focused_queries, broad_query)):
        if not query:
            continue
        search = _api(
            {
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": "12", "format": "json",
            }
        )
        for item in search.get("query", {}).get("search", []):
            page_title = str(item.get("title") or "").strip()
            if not page_title or page_title.casefold() in seen_pages:
                continue
            seen_pages.add(page_title.casefold())
            parsed = _api(
                {
                    "action": "parse", "page": page_title, "prop": "text",
                    "format": "json", "formatversion": "2",
                }
            )
            page_html = str(parsed.get("parse", {}).get("text") or "")
            if re.match(r"^(?:List of songs recorded by\s+|.+\s+discography$)", page_title, flags=re.I):
                discography = _extract_discography_song_metadata(
                    page_html, wanted_titles, page_title, artist_text
                )
                if discography:
                    return discography
                continue
            if artist_text and _artists_overlap(artist_text, page_title):
                discography = _extract_discography_song_metadata(
                    page_html, wanted_titles, page_title, artist_text
                )
                if discography:
                    return discography
                continue
            tracks = extract_tracks_from_html(page_html)
            exact = next(
                (
                    track for track in tracks
                    if _song_title_key(str(track.get("title") or "")) in wanted_titles
                ),
                None,
            )
            if exact is None:
                discography = _extract_discography_song_metadata(
                    page_html, wanted_titles, page_title, artist_text
                )
                if discography:
                    return discography
                continue
            track_artists = str(exact.get("artists") or "").strip()
            if artist_text and not _artists_overlap(artist_text, track_artists):
                continue
            album = normalize_album_name(re.sub(
                r"\s*\((?:original\s+)?soundtrack\s*\)\s*$|\s+soundtrack\s*$",
                "",
                page_title,
                flags=re.I,
            ).strip())
            if not album:
                continue
            page_year = re.search(r"\b(?:19|20)\d{2}\b", page_title)
            return {
                "title": str(exact.get("title") or "").strip(),
                "album": album,
                "artists": track_artists,
                "year": page_year.group() if page_year else "",
                "page_title": page_title,
                "language": _page_language(page_title, page_html),
            }
    return {}


def _extract_discography_song_metadata(
    html: str,
    wanted_titles: set[str],
    page_title: str,
    artists_hint: str = "",
) -> dict[str, str]:
    """Read album/film and year from an exact row in a singer discography table.

    Wikipedia discographies commonly place the year in a section heading and use
    row-spanned cells.  We deliberately require both an exact song cell and an
    album/film cell in that same parsed row; ambiguous continuation rows are ignored.
    """

    parser = _TableParser()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        headers = [_key(cell) for cell in table[0]]
        album_index = next(
            (index for index, header in enumerate(headers) if header in {"album", "film", "album single"}),
            -1,
        )
        title_index = next(
            (index for index, header in enumerate(headers) if header in {"song", "title", "track"}),
            -1,
        )
        year_index = next(
            (index for index, header in enumerate(headers) if header == "year"),
            -1,
        )
        artist_index = next(
            (
                index for index, header in enumerate(headers)
                if "co artist" in header or "co singer" in header
                or header in {"artist", "artists", "singer", "singers", "vocals"}
            ),
            -1,
        )
        language_index = next(
            (index for index, header in enumerate(headers) if header == "language"),
            -1,
        )
        if album_index < 0:
            continue
        if title_index < 0:
            continue
        for row in table[1:]:
            if max(album_index, title_index) >= len(row):
                continue
            title = row[title_index].strip().strip('"')
            if _song_title_key(title) not in wanted_titles:
                continue
            album = row[album_index].strip().strip('"')
            if not album:
                continue
            lead_artist = re.sub(
                r"^List of songs recorded by\s+|\s+discography$",
                "", page_title, flags=re.I,
            ).strip()
            co_artists = row[artist_index].strip() if 0 <= artist_index < len(row) else ""
            evidence_artists = format_artist_names(
                ", ".join(part for part in (lead_artist, co_artists) if part)
            )
            if artists_hint and not _artists_overlap(artists_hint, evidence_artists):
                continue
            year = row[year_index].strip() if 0 <= year_index < len(row) else ""
            year_match = re.search(r"\b(?:19|20)\d{2}\b", year)
            if year_match is None:
                section_year = _year_before_table_occurrence(html, title)
                year_match = re.search(r"\b(?:19|20)\d{2}\b", section_year)
            language = (
                row[language_index].strip()
                if 0 <= language_index < len(row)
                else _language_before_table_occurrence(html, title)
            )
            return {
                "title": title,
                "album": album,
                "artists": evidence_artists,
                "year": year_match.group() if year_match else "",
                "page_title": page_title,
                "language": _supported_language(language),
            }
    return {}


def _year_before_table_occurrence(html: str, title: str) -> str:
    """Find a year heading for a title occurrence that is actually inside a table."""

    lowered = html.casefold()
    needle = title.casefold()
    offset = 0
    while needle:
        occurrence = lowered.find(needle, offset)
        if occurrence < 0:
            break
        table_start = lowered.rfind("<table", 0, occurrence)
        table_end = lowered.rfind("</table", 0, occurrence)
        if table_start > table_end:
            headings = re.findall(
                r"<h[2-4][^>]*>[\s\S]*?\b((?:19|20)\d{2})\b[\s\S]*?</h[2-4]>",
                html[:table_start],
                flags=re.I,
            )
            return headings[-1] if headings else ""
        offset = occurrence + len(needle)
    return ""


def _supported_language(value: str) -> str:
    match = re.search(
        r"\b(Hindi|Bengali|Tamil|Telugu|Malayalam|Kannada|Marathi|Punjabi)\b",
        str(value or ""),
        flags=re.I,
    )
    return match.group(1).title() if match else ""


def _page_language(page_title: str, html: str) -> str:
    """Read a film/soundtrack language without guessing from the song text."""

    language = _supported_language(page_title)
    if language:
        return language
    film_link = re.search(
        r'title="[^"]*\((?:19|20)\d{2}\s+'
        r'(Hindi|Bengali|Tamil|Telugu|Malayalam|Kannada|Marathi|Punjabi)\s+film\)"',
        html,
        flags=re.I,
    )
    return film_link.group(1).title() if film_link else ""


def _language_before_table_occurrence(html: str, title: str) -> str:
    """Find a language section heading associated with a discography table."""

    lowered = html.casefold()
    occurrence = lowered.find(title.casefold())
    if occurrence < 0:
        return ""
    table_start = lowered.rfind("<table", 0, occurrence)
    if table_start < 0:
        return ""
    headings = re.findall(
        r"<h[2-4][^>]*>([\s\S]*?)</h[2-4]>", html[:table_start], flags=re.I
    )
    for heading in reversed(headings):
        language = _supported_language(re.sub(r"<[^>]+>", " ", heading))
        if language:
            return language
    return ""


def _artists_overlap(left: str, right: str) -> bool:
    """Require all multi-artist hints, but tolerate one partial artist credit."""

    def names(value: str) -> set[str]:
        return {
            _key(part)
            for part in re.split(r"\s*(?:,|&|\band\b|\bfeat(?:uring)?\.?\b)\s*", value, flags=re.I)
            if _key(part)
        }

    left_names = names(left)
    right_names = names(right)
    if not left_names or not right_names:
        return False
    matched = sum(
        any(
            wanted == available or wanted in available or available in wanted
            for available in right_names
        )
        for wanted in left_names
    )
    return matched > 0 if len(left_names) == 1 else matched == len(left_names)
