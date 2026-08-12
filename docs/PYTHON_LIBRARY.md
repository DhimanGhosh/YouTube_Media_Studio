# Python wheel and library guide

This guide documents the supported Python API shipped in the YouTube Media Studio
wheel. The library calls the same Qt-independent operation executor used by the
desktop forms, so scripts receive the same validation, processing behavior, and
structured summaries as GUI jobs.

> [!IMPORTANT]
> Download only media you are authorized to use. Operations that edit, rename,
> move, replace, or delete files should first be tested on backed-up sample data.

## Requirements

- Python 3.11 or newer
- A supported YouTube Media Studio wheel
- FFmpeg and FFprobe for media conversion, trimming, splitting, and probing
- Network access for YouTube downloads and online metadata sources
- Optional Ollama or hosted-provider credentials for AI-assisted workflows

The wheel declares its Python dependencies. Pip installs those dependencies
automatically. PyQt6 is not required for library-only use.

## Download the wheel

Download the newest wheel from
[GitHub Releases](https://github.com/DhimanGhosh/YouTube_Media_Studio/releases/latest).
Its filename follows this installable wheel format:

```text
youtube_media_studio-v<version>-py3-none-any.whl
```

The `py3-none-any` compatibility tags are required by the Python wheel standard.

## Install with pip

From the directory containing the downloaded wheel:

```console
python -m pip install ./youtube_media_studio-v2.9.1-py3-none-any.whl
```

Using a virtual environment is recommended:

```console
python -m venv .venv
```

Activate it on Windows:

```console
.venv\Scripts\activate
```

Or on macOS and Linux:

```console
. .venv/bin/activate
```

Then install the wheel:

```console
python -m pip install ./youtube_media_studio-v2.9.1-py3-none-any.whl
```

## Upgrade an existing installation

Install the newer wheel with `-U`/`--upgrade`:

```console
python -m pip install -U ./youtube_media_studio-v2.9.1-py3-none-any.whl
```

Pip identifies every release as the same `youtube-media-studio` distribution.
It removes the older installed version and installs the newer version in its
place. Application data and media files are not stored inside the Python package
and are not removed by a package upgrade.

If the distribution is later published to a configured Python package index, the
package-name form is:

```console
python -m pip install -U youtube-media-studio
```

At present, GitHub Releases is the authoritative wheel source, so use the local
wheel path or a direct release-asset URL rather than assuming PyPI availability.

Verify the installed version:

```console
python -c "from importlib.metadata import version; print(version('youtube-media-studio'))"
```

Inspect the installation:

```console
python -m pip show youtube-media-studio
python -m pip check
```

Uninstall only the Python package and its installed files:

```console
python -m pip uninstall youtube-media-studio
```

## Public imports

```python
from youtube_audio_video_downloader import (
    CancellationToken,
    MediaStudio,
    Operation,
    OperationSummary,
    SUPPORTED_OPERATIONS,
    run_operation,
)
```

The installable distribution is named `youtube-media-studio`; the Python import
package remains `youtube_audio_video_downloader` for compatibility with existing
scripts.

## Choose an API style

### Reusable client

`MediaStudio` merges reusable defaults into every call:

```python
from youtube_audio_video_downloader import MediaStudio

studio = MediaStudio(
    defaults={
        "ai_enabled": False,
        "workers": 4,
        "retries": 3,
    }
)

summary = studio.format_artists(input_text="Asha Bhosle & Kishore Kumar")
print(summary.output_text)
```

Values are merged in this order, with later values winning:

1. Client `defaults`
2. The optional `params` mapping passed to a method
3. Keyword arguments passed to that method

### Generic dispatch

Use `run_operation` when the operation name is selected dynamically:

```python
from youtube_audio_video_downloader import Operation, run_operation

summary = run_operation(
    Operation.PARSE_TRACKS,
    {
        "input_text": "00:00 First song\n03:45 Second song",
        "unknown_artists": "Unknown",
    },
    keep_case=False,
)
```

String names are also accepted:

```python
summary = run_operation("format_artists", input_text="Artist One feat. Artist Two")
```

### Discover operations

```python
from youtube_audio_video_downloader import MediaStudio, SUPPORTED_OPERATIONS

print(SUPPORTED_OPERATIONS)
print(MediaStudio().operations)
```

Both return these 16 workflow names:

| Operation | Client method | Purpose |
| --- | --- | --- |
| `audio` | `studio.audio(...)` | Download/tag MP3 audio jobs |
| `video` | `studio.video(...)` | Inspect or download video/audio jobs |
| `album` | `studio.album(...)` | Split an album source into tracks |
| `jukebox` | `studio.jukebox(...)` | Split compilation sources into songs |
| `track_reorder` | `studio.track_reorder(...)` | Rewrite track-number tags in a chosen order |
| `audio_trimmer` | `studio.audio_trimmer(...)` | Trim an existing local audio file |
| `redownload` | `studio.redownload(...)` | Replace or copy media from a YouTube source |
| `edit_media` | `studio.edit_media(...)` | Edit metadata, trim, or redownload one file |
| `edit_album` | `studio.edit_album(...)` | Apply shared metadata across an album folder |
| `album_consolidator` | `studio.album_consolidator(...)` | Move media into verified album folders |
| `album_metadata_enricher` | `studio.album_metadata_enricher(...)` | Verify and complete album metadata |
| `duplicate_links` | `studio.duplicate_links(...)` | Report duplicate YouTube links in JSON |
| `format_artists` | `studio.format_artists(...)` | Normalize an artist string |
| `parse_tracks` | `studio.parse_tracks(...)` | Convert timestamp text into track JSON |
| `search_song` | `studio.search_song(...)` | Interpret a request and search YouTube |
| `enrich_song` | `studio.enrich_song(...)` | Verify metadata for one selected result |

## Return value

Every operation returns an immutable `OperationSummary`:

| Field | Type | Meaning |
| --- | --- | --- |
| `operation` | `str` | Executed operation name |
| `total` | `int` | Total work items reported |
| `downloaded` | `int` | Newly downloaded/generated items |
| `moved` | `int` | Files moved to another folder |
| `deleted` | `int` | Confirmed duplicates deleted by consolidation |
| `reordered` | `int` | Track numbers reordered |
| `tagged` | `int` | Files whose metadata was written |
| `skipped` | `int` | Items intentionally not changed |
| `tracked` | `int` | Items recorded by metadata tracking |
| `listed` | `int` | Items listed in inspection mode |
| `failed` | `int` | Failed items |
| `output_text` | `str` | Text/JSON result for utility and search operations |
| `output_path` | `str` | Primary output file or directory when applicable |
| `completed_items` | `tuple[str, ...]` | Successful item labels |
| `failed_items` | `tuple[str, ...]` | Failed/skipped item labels |

Convert a summary into JSON-compatible values:

```python
import json

print(json.dumps(summary.as_dict(), indent=2, ensure_ascii=False))
```

A completed call can still report nonzero `failed` or `skipped` counts. Inspect the
summary instead of assuming that a returned value means every individual item
succeeded.

## Common operation parameters

Downloader, album, and jukebox operations share these optional settings:

| Parameter | Default | Description |
| --- | --- | --- |
| `workers` | Machine-derived | Parallel worker count; must be at least 1 |
| `min_delay` | `10` | Minimum randomized delay in seconds |
| `max_delay` | `25` | Maximum randomized delay in seconds |
| `retries` | `3` | Maximum attempts; must be at least 1 |
| `retry_wait` | `60` | General retry wait in seconds |
| `rate_limit_wait` | `180` | Rate-limit wait in seconds |
| `preferred_mp3_quality` | `"320"` | Preferred MP3 bitrate |
| `audio_sample_rate` | `"44100"` | Output audio sample rate |
| `overwrite` | `False` | Replace existing output when supported |
| `ai_enabled` | `True` | Enable model preflight when a model is configured |
| `agentic_model` | `""` | Model/provider selection understood by the app |
| `auto_enrich_downloads` | `True` | Enrich successful downloaded audio |
| `auto_consolidate_downloads` | `True` | Organize enriched audio under its output root |
| `tracker_path` | unset | Optional metadata-completion tracker JSON path |

Set `ai_enabled=False` for deterministic/internet-only processing. Online catalog,
Wikipedia, YouTube, and other evidence lookups may still use the network.

## Input data versus JSON files

The four batch operations accept either an in-memory `input_data` mapping or a
path to an existing JSON job file. Prefer `input_data` in application code.

### Audio download

```python
summary = studio.audio(
    input_data={
        "Example song": {
            "ytb_link": "https://www.youtube.com/watch?v=VIDEO_ID",
            "file_name": "Title - Album - Artists",
            "album_art": "https://example.com/cover.jpg",
            "release_year": "2024",
            "start_timestamp": "00:00",
            "end_timestamp": "03:30",
        }
    },
    mode="download",
    output_dir="downloads/songs",
)
```

Audio-specific parameters:

| Parameter | Default | Description |
| --- | --- | --- |
| `input_data` | unset | Non-empty audio job mapping |
| `input_path` | unset | JSON job file used when `input_data` is absent |
| `mode` | `"download"` | `download` or `tag-existing` |
| `output_dir` | `songs` for in-memory input | MP3 output directory |

### Video download or inspection

```python
summary = studio.video(
    input_data={
        "Example video": {
            "ytb_link": "https://www.youtube.com/watch?v=VIDEO_ID",
            "file_name": "Example video",
            "resolution": "1080p",
        }
    },
    output_dir="downloads/videos",
    audio_output_dir="downloads/songs",
    resolution="best",
    mp3_mode="audio-only",
    merge_format="mp4",
    write_report=True,
)
```

| Parameter | Default | Description |
| --- | --- | --- |
| `input_data` / `input_path` | required | In-memory mapping or JSON job path |
| `resolution` | `"best"` | Default quality; `ask` is not supported by the API |
| `mp3_mode` | `"audio-only"` | MP3 behavior for applicable entries; `both` is also supported |
| `output_dir` | `videos` for in-memory input | Video destination |
| `audio_output_dir` | `songs` for in-memory input | Extracted MP3 destination |
| `merge_format` | `"mp4"` | Merge container such as `mp4`, `mkv`, or `webm` |
| `info_mode` | `False` | Inspect/list formats without downloading |
| `write_report` | `True` | Write the result report |

### Album splitter

Use `input_data`, a JSON file in `input_value`, or a direct YouTube URL in
`input_value`:

```python
summary = studio.album(
    input_value="https://www.youtube.com/watch?v=ALBUM_VIDEO_ID",
    album_name="Example Album",
    artists="Example Artist",
    output_dir="downloads/album_tracks",
    silence_threshold_db=-35.0,
    min_silence_duration=1.5,
    min_track_duration=45.0,
    trim_silence_padding=0.25,
    keep_temp=False,
    write_report=True,
)
```

In-memory entries can contain album identity, artwork, release year, and `tracks`.
Each track can specify `start`/`end` (or `stop`), `artists`, `ytb_link`, and a
`download` flag.

### Jukebox splitter

```python
summary = studio.jukebox(
    input_data={
        "Example compilation": {
            "ytb_link": "https://www.youtube.com/watch?v=JUKEBOX_ID",
            "tracks": [
                {"First song": {"start": "00:00", "end": "03:45", "artists": "Artist"}},
                {"Second song": {"start": "03:46", "end": "07:20", "artists": "Artist"}},
            ],
        }
    },
    output_dir="downloads/jukebox_tracks",
    keep_temp=False,
    overwrite=False,
    write_report=True,
)
```

## Local-file operations

### Reorder track numbers

Paths must already be in the desired order:

```python
summary = studio.track_reorder(
    paths=["album/first.mp3", "album/second.mp3", "album/third.mp3"],
    retries=3,
    ai_enabled=False,
)
```

Only track-number tags are changed.

### Trim audio

```python
summary = studio.audio_trimmer(
    input_path="song.mp3",
    start_timestamp="00:30",
    end_timestamp="03:45",
    overwrite_source=False,
    output_path="song_trimmed.mp3",
    ai_enabled=False,
)
```

An empty `end_timestamp` trims to the end. Supply an `output_path` when
`overwrite_source=False`.

### Redownload media

```python
summary = studio.redownload(
    input_path="old_song.mp3",
    youtube_url="https://www.youtube.com/watch?v=VIDEO_ID",
    media_mode="auto",
    start_timestamp="00:00",
    end_timestamp="",
    overwrite_source=True,
    ai_enabled=False,
)
```

`media_mode` accepts `auto`, `audio`, `video`, or `both` as appropriate for the
source and desired output.

### Edit one media file

```python
summary = studio.edit_media(
    action="metadata",
    input_path="song.mp3",
    overwrite_source=True,
    metadata={
        "title": "Correct Title",
        "album": "Correct Album",
        "artists": "Artist One, Artist Two",
        "year": "2024",
        "track_number": "1",
        "track_total": "10",
    },
    artwork_path="cover.jpg",
    remove_artwork=False,
    ai_enabled=False,
)
```

`action` accepts `metadata`, `trim`, or `redownload`. Trim/redownload also use
`start_timestamp`, `end_timestamp`, `output_path`, and `overwrite_source`.
Redownload additionally uses `youtube_url` and `media_mode`. Do not set both
`artwork_path` and `remove_artwork=True`.

### Edit an album folder

```python
summary = studio.edit_album(
    folder="albums/Example Album",
    metadata={
        "album": "Example Album",
        "year": "2024",
        "artists": "",  # blank preserves each track's existing artists
    },
    artwork_path="cover.jpg",
    remove_artwork=False,
    ai_enabled=False,
)
```

Titles and track numbers are preserved. The operation can rename files after
metadata changes and applies updates recursively to supported media.

### Enrich album metadata

```python
summary = studio.album_metadata_enricher(
    source_folder="incoming/album",
    destination_folder="organized",  # optional additional scan root
    workers=4,
    retries=3,
    tracker_path="state/metadata-completion.json",
    force_recheck=False,
    wikipedia_track_order=True,
    ai_enabled=False,
)
```

### Consolidate albums

```python
summary = studio.album_consolidator(
    source_folder="incoming",
    destination_folder="organized",
    perform_enrichment=True,
    enrich_all_destination=False,
    workers=4,
    retries=3,
    tracker_path="state/metadata-completion.json",
    ai_enabled=False,
)
```

This operation can retag, rename, move, reorder, and delete confirmed duplicate
files. Back up both roots before automating it.

## Utility and search operations

### Find duplicate links

```python
summary = studio.duplicate_links(
    input_path="config/songs.json",
    output_path="reports/duplicates.json",
    ai_enabled=False,
)
duplicates_json = summary.output_text
```

### Format artist names

```python
summary = studio.format_artists(
    input_text="Artist One feat. Artist Two & Artist Three",
    ai_enabled=False,
)
print(summary.output_text)
```

### Parse timestamp text

```python
summary = studio.parse_tracks(
    input_text="00:00 First song\n03:45 Second song",
    end_field="end",
    keep_case=False,
    unknown_artists="Unknown",
    output_path="tracks.json",
    ai_enabled=False,
)
```

Use `input_path` instead of `input_text` to read a UTF-8 timestamp file.

### Search for a song

```python
import json

summary = studio.search_song(
    request_text="Find the official video for an example song",
    model="qwen2.5:7b",
    limit=8,
    ai_enabled=False,
)
payload = json.loads(summary.output_text)
print(payload["intent"])
print(payload["results"])
```

### Enrich a selected search result

```python
summary = studio.enrich_song(
    url="https://www.youtube.com/watch?v=VIDEO_ID",
    title="Candidate title",
    album="Candidate album",
    artists="Candidate artist",
    thumbnail="https://example.com/thumbnail.jpg",
    request_text="Original search request",
    model="qwen2.5:7b",
    ai_enabled=False,
)
verified = json.loads(summary.output_text)
```

## Cooperative cancellation

Operations are synchronous. To cancel one from another thread, share a token or
call `MediaStudio.cancel()`:

```python
from concurrent.futures import ThreadPoolExecutor
from youtube_audio_video_downloader import MediaStudio

studio = MediaStudio(defaults={"ai_enabled": False})

with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(
        studio.audio,
        input_path="config/songs.json",
        output_dir="downloads/songs",
    )
    # From another event handler or control path:
    studio.cancel()
    try:
        future.result()
    finally:
        studio.reset_cancellation()
```

Cancellation is cooperative: a network or external-tool call may need to return
before the operation observes the request. Do not run two simultaneous operations
through one `MediaStudio` instance because they would share one cancellation token.

## Exceptions and logging

Invalid input raises normal Python exceptions such as `ValueError`,
`FileNotFoundError`, `OSError`, or project-specific cancellation/file-access
exceptions. Wrap calls at the automation boundary:

```python
try:
    summary = studio.parse_tracks(input_path="timestamps.txt", ai_enabled=False)
except (FileNotFoundError, ValueError) as exc:
    print(f"Job was not started: {exc}")
```

The shared executor prints progress and diagnostic lines to standard output. Use
`contextlib.redirect_stdout`, normal process log capture, or a logging wrapper if
your application needs to retain those messages.

## Upgrade checklist

1. Download the newest `.whl` and `SHA256SUMS.txt` from the same GitHub Release.
2. Verify the wheel checksum with a platform SHA-256 tool.
3. Activate the same virtual environment that contains the previous version.
4. Run `python -m pip install -U ./youtube_media_studio-v<new-version>-py3-none-any.whl`.
5. Run the version and `pip check` commands shown above.
6. Run a non-destructive utility call before starting file-changing automation.

The release workflow tests this replacement path by installing the previous
release wheel, installing the newly built wheel with pip, and asserting that the
installed distribution reports the new version.
