# YouTube Media Studio user guide

This guide explains how to use the installed desktop application. No Python setup or
command line is required. For installation instructions, supported platforms, and
downloads, return to the [main README](../README.md#download).

## Contents

- [First-time setup](#first-time-setup)
- [How the interface works](#how-the-interface-works)
- [Screen reference](#screen-reference)
- [Find and download a song](#find-and-download-a-song)
- [Split an album or jukebox](#split-an-album-or-jukebox)
- [Edit a local media file](#edit-a-local-media-file)
- [Enrich and organize an existing music folder](#enrich-and-organize-an-existing-music-folder)
- [Use the local media library](#use-the-local-media-library)
- [Read Live Logs](#read-live-logs)
- [File-safety rules](#file-safety-rules)
- [Troubleshooting](#troubleshooting)

> [!IMPORTANT]
> Download only media you are authorized to use. Before running metadata, editing,
> consolidation, or duplicate-removal tools over a valuable library, keep a backup
> and test the workflow on a small folder first.

## First-time setup

1. Start **YouTube Media Studio** from the Windows Start menu, macOS Applications, or
   the Linux application menu.
2. Open **Global Settings** and confirm the application data directory, download
   defaults, audio choices, network retries, and worker limits.
3. AI assistance is optional. Leave **Use AI for this task** off for deterministic
   internet/catalog matching. To use AI, configure a provider and model in **Global
   Settings** first.
4. Open **Search Song** if you know what you want but do not have a source URL. Go
   directly to a downloader or splitter when you already have one.
5. Watch active work on **Dashboard** and open **Live Logs** for the complete result of
   each item.

The installer bundles the desktop runtime and required media tools. Normal desktop use
does not require a separate Python, FFmpeg, FFprobe, Deno, or yt-dlp installation.

## How the interface works

The left sidebar switches between workflows. Each workflow collects its input in one
or more cards and starts only when its main action button is selected. Tasks run in the
background, so changing pages does not cancel an operation.

- **Dashboard** shows current activity, totals, and recent task results.
- **Live Logs** retains the detailed per-file explanation.
- **Global Settings** controls defaults shared by the other pages.
- A **Browse** button selects a local file or folder without requiring a typed path.
- **Use AI for this task** affects the current supported workflow; it is not required
  for ordinary downloads, edits, playback, or deterministic metadata matching.

## Screen reference

| Sidebar page | What it does | How to use it |
| --- | --- | --- |
| **Dashboard** | Shows current activity, session totals, and recent operations. | Start work on another page, then return here to see progress and results. **Clear** removes displayed session history, not media files. |
| **Search Song** | Finds a song, album, movie track, video, or jukebox from plain language. | Enter a description, review and preview the matches, choose a row and destination workflow, then select **Use selected result**. |
| **Audio Downloader** | Downloads permitted audio with a normalized filename, tags, and cover artwork. | Add or import track details, verify title/album/artists/year and output location, then run the download. |
| **Video Downloader** | Inspects available formats and downloads video or audio. | Enter the source URL, inspect formats, choose quality and output, then start the download. |
| **Album Splitter** | Turns one full-album source into separate tagged songs. | Add the source and timestamped track list, verify the rows and shared album metadata, then split. |
| **Jukebox Splitter** | Splits a compilation containing tracks from different albums or artists. | Add the source, review each timestamped track and its individual metadata, then split and organize. |
| **Track Reorder** | Applies a verified album order to existing local tracks. | Select an album folder, preview the proposed sequence, and apply it only after checking the matches. |
| **Edit File** | Trims a local file and repairs its filename, tags, track number, or artwork. | Select a file, load its current values, change only the required fields or trim range, then save. |
| **Album Consolidator** | Enriches local metadata and routes verified files into album folders. | Select the source, run the enricher, inspect review items, select a destination, then move verified tracks. |
| **Utilities** | Checks duplicate source links, formats artist names, and converts timestamp text. | Choose the relevant tab, paste or load input, run the tool, then copy or save its result. |
| **Live Logs** | Explains what an operation changed, skipped, or could not verify. | Filter or copy the relevant block when troubleshooting or reporting a bug. |
| **Global Settings** | Controls output defaults, concurrency, retries, networking, audio, and optional AI providers. | Change and save a setting before starting the workflow that uses it. A provider key is needed only for that provider. |
| **Media Library** | Scans, browses, searches, plays, queues, and manages local media. | Add library locations, search by track/artist/album, then use the player or context actions. |

## Find and download a song

1. Open **Search Song** and describe the song with identifying information such as
   title, singers, film or album, and language.
2. Select **Understand and search**.
3. Preview the results and select the correct source.
4. Set **Send selected result to** to **Audio Downloader (MP3 + metadata)** and select
   **Use selected result**.
5. In **Audio Downloader**, review the populated title, album, artists, year, artwork,
   and output folder. Do not assume the first result is correct.
6. Start the download and check **Live Logs** for the final saved path.

The same result-routing control can send a match to **Video Downloader**, **Album
Splitter**, or **Jukebox Splitter**.

## Split an album or jukebox

1. Open **Album Splitter** for a single release or **Jukebox Splitter** for a mixed
   compilation.
2. Add the source and timestamps. **Utilities → Timestamp parser** can turn a copied
   track list into structured rows.
3. Verify every title and time boundary; a wrong timestamp affects adjacent tracks.
4. Complete the album, artist, year, and artwork fields. For a jukebox, review the
   per-track values as well.
5. Choose the output and start the split.
6. Listen to the produced boundaries and inspect the tags before deleting the original
   long-form source.

## Edit a local media file

1. Open **Edit File** and browse to the media file.
2. Load its current duration and metadata.
3. Set a start/end time only when trimming is required.
4. Correct the title, album, artists, year, track position, or artwork as needed.
5. Review the selected output behavior, save, and verify the result in **Live Logs**.

The Media Library can send a selected local track directly to this page with its
metadata already loaded.

## Enrich and organize an existing music folder

The Album Consolidator has two separate stages. **Album enricher** repairs metadata but
does not move files. **Move into album folders** routes approved files to the selected
library.

1. Under **1. Album enricher**, select the source folder containing incoming tracks.
2. Run **Album enricher** and inspect `[METADATA-REVIEW]` or `[ENRICH-SKIPPED]` lines.
   Correct unresolved files with **Edit File**, improve their filenames, or rerun when
   better catalog evidence is available.
3. Under **2. Move into album folders**, select the destination library. The source is
   still the folder selected in stage 1.
4. Leave **Include all destination files in enrichment** off to process the incoming
   scope only. Enable it only when the complete destination tree should also be
   enriched.
5. Select **Move into album folders**. Approved files are routed into an
   `Album (Year)` folder. Unresolved files remain in the source for manual review.

When AI is enabled, the move action performs an additional pre-move identity check.
Only paths reported as fully complete are admitted to the mover. `[AI-VERIFIED]` means
the model found supporting evidence; it does **not** by itself guarantee that all
required tags and artwork are complete. `[AGENT-REVIEW]` is the final indication that
the safety gate left a file in the source.

### Why a track may remain in the source

- Album, title, artist, year, or required artwork is blank or unresolved.
- Internet/catalog candidates conflict or do not match the recording duration.
- The AI verifier requested review even though its text mentions supporting evidence.
- The configured AI provider failed and a fallback model produced a different result.
- The file has an invalid or artist-contaminated album value.
- The operation could not read the media or its metadata.

Use the final per-file message in **Live Logs** as the authoritative outcome. If the
message and preceding AI explanation appear inconsistent, keep the file untouched and
report the complete log block.

## Use the local media library

1. Open **Media Library** and add or scan the folders containing audio/video.
2. Search by title, performer, or album; use artist and album views to narrow a large
   collection.
3. Play a result, add tracks to the queue, and use shuffle or repeat as required.
4. Use a file's context actions to open its folder or send it to **Edit File**.
5. If a requested song is absent locally, continue the request in **Search Song**.

## Read Live Logs

| Log marker | Meaning |
| --- | --- |
| `[START]` / `[PROGRESS]` | A task began and is still working through its input. |
| `[COMPLETE]` | The operation ended. Read the item/skip counts and preceding per-file messages. |
| `[ENRICHED]` / `[MOVED]` | Metadata was written or a file was routed successfully. |
| `[SKIPPED]` / `[ENRICH-SKIPPED]` | The application intentionally made no change; the line states why. |
| `[AI-VERIFIED]` | The model found identity evidence; completeness and operation rules still apply. |
| `[AI-REVIEW]` / `[METADATA-REVIEW]` | Evidence was ambiguous or incomplete and needs review. |
| `[AGENT-REVIEW]` | An AI-enabled safety gate did not approve the file, so it remains in the source. |
| `[AI-PROVIDER-FALLBACK]` | The selected provider was unavailable and a configured fallback was used. |
| `[ERROR]` / `[FAILED]` | The operation could not finish that item. Copy the surrounding lines when opening an issue. |

`[COMPLETE]` describes the end of a task, not a guarantee that every input file was
changed. Always compare its processed and skipped counts.

## File-safety rules

- Existing destination files are not overwritten.
- Unresolved Album Consolidator inputs remain in their source folder.
- If the same normalized title already exists in the matching destination album, the
  incoming source is treated as a duplicate. Keep a backup until you are confident in
  the matching behavior.
- Album folder names and generated filenames are sanitized for the operating system.
- Closing or switching a page does not necessarily cancel its background worker. Use
  the application's cancellation control and confirm the log outcome.

## Troubleshooting

1. Open **Live Logs** and find the block from `[START]` through `[COMPLETE]` or
   `[FAILED]` for the affected operation.
2. Check whether the outcome was a deliberate `[SKIPPED]`, a verification review, a
   provider fallback, or an actual error.
3. Confirm that source and destination paths exist and are writable.
4. For metadata work, inspect the file in **Edit File** for missing title, album,
   artists, year, track information, or artwork.
5. If AI was enabled, retry with it disabled to compare deterministic behavior, or fix
   the configured provider instead of assuming a fallback model is equivalent.
6. When reporting a bug, include the application version, operating system, complete
   log block, source file's current metadata, and the expected result. Do not publish
   API keys, private paths you do not want exposed, or copyrighted media files.

For command-line diagnostics, installer issues, and development setup, see the
[README troubleshooting section](../README.md#troubleshooting).
