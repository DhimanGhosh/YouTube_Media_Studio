# Workflow designs

These diagrams describe runtime behavior. The [low-level design](LOW_LEVEL_DESIGN.md)
identifies the owning modules and classes.

## 1. Desktop operation lifecycle

```mermaid
sequenceDiagram
    actor User
    participant Page as Task page
    participant Window as MainWindow
    participant Worker as OperationWorker
    participant Dispatch as execute_operation
    participant Service as Selected service

    User->>Page: Fill form and select Start
    Page->>Window: Operation key and parameters
    Window->>Worker: Create and move to QThread
    Worker-->>Window: Progress 0/N and START log
    Worker->>Dispatch: execute_operation(key, params, token)
    Dispatch->>Dispatch: Log AI usage and optional preflight
    Dispatch->>Service: Invoke handler
    loop Per item or phase
        Service-->>Worker: stdout/stderr progress lines
        Worker-->>Window: Log, phase, progress, item result
    end
    Service-->>Dispatch: OperationSummary
    Dispatch-->>Worker: Summary
    Worker-->>Window: finished(summary)
    Window->>Window: Update dashboard and refresh library
```

## 2. Per-entry Audio Downloader

```mermaid
sequenceDiagram
    actor User
    participant Editor as Audio batch editor
    participant Loader as load_songs
    participant Service as YouTubeAudioDownloader
    participant Range as download_range
    participant YTDLP as yt-dlp
    participant Tagger as MetadataTagger

    User->>Editor: Add songs with independent start/end
    Editor->>Loader: JSON dictionary
    Loader-->>Service: list of Song models
    par For each Song in bounded pool
        Service->>Range: Validate this Song's timestamps
        Range-->>Service: Full-source or section options
        Service->>YTDLP: Download best audio with entry options
        YTDLP-->>Service: Source converted to MP3
        Service->>Tagger: Apply title, album, artists, year, art, track number
        Tagger-->>Service: Tagged MP3
    end
    Service-->>Editor: Per-song DownloadResult report
```

Tag-existing mode hides source and timestamp fields because it changes tags and names
without downloading or trimming media.

## 3. Per-entry Video Downloader

```mermaid
sequenceDiagram
    actor User
    participant Editor as Video batch editor
    participant Loader as load_videos
    participant Video as YouTubeVideoDownloader
    participant YTDLP as yt-dlp
    participant Audio as YouTubeAudioDownloader

    User->>Editor: Add videos with URL, quality, start/end
    Editor->>Loader: JSON dictionary
    Loader-->>Video: list of VideoJob models
    Video->>YTDLP: Extract format metadata
    Video->>Video: Prepare all media selections on main worker
    par Execute prepared plans
        alt Video or both
            Video->>YTDLP: Download selected formats using VideoJob range
        end
        alt MP3 or both
            Video->>Audio: Build Song carrying VideoJob range
            Audio->>YTDLP: Download MP3 using the same interval
        end
    end
    Video-->>Editor: Ordered results and optional report
```

## 4. Album and jukebox splitting

```mermaid
flowchart TD
    Job["Album or jukebox job"] --> Source{"Local source or YouTube URL?"}
    Source -->|YouTube| Download["Download temporary source"]
    Source -->|Local| Probe["Probe duration"]
    Download --> Probe
    Probe --> Segments{"Timestamp tracks supplied?"}
    Segments -->|Yes| Validate["Validate ordered boundaries"]
    Segments -->|No| Silence["Detect silence and build segments"]
    Validate --> Render["FFmpeg render each segment"]
    Silence --> Render
    Render --> Tag["Apply track metadata and artwork"]
    Tag --> Number{"Track numbering enabled?"}
    Number -->|Yes| Reorder["Write track/total"]
    Number -->|No| Report["Result report"]
    Reorder --> Report
    Report --> Cleanup["Remove temporary source unless retained"]
```

Jukebox splitting subclasses the album splitter and adds compilation-oriented metadata
and output organization.

## 5. Metadata enrichment and album consolidation

```mermaid
sequenceDiagram
    actor User
    participant Worker as OperationWorker
    participant Enricher as album_metadata_enricher
    participant Evidence as Catalog and web sources
    participant Verifier as metadata_verifier
    participant Writer as media_metadata
    participant Consolidator as album_consolidator
    participant Reorder as track_reorder

    User->>Worker: Enrich and/or move folder
    alt Perform enrichment
        Worker->>Enricher: Scan supported files
        par Candidate files
            Enricher->>Evidence: Fetch identity, album, year, art, track evidence
            Evidence-->>Enricher: Independent source records
            Enricher->>Verifier: Local values + candidate evidence
            Verifier-->>Enricher: Apply, unchanged, or review
            opt Apply
                Enricher->>Writer: Replace tags and safe filename
            end
        end
    end
    Worker->>Consolidator: Route verified files into canonical album folders
    Consolidator->>Consolidator: Detect duplicates and resolve collisions
    Consolidator->>Reorder: Apply verified album ordering
    Reorder-->>Worker: Updated count
    Worker-->>User: Summary and review/skipped details
```

When **Perform album enrichment** is disabled, consolidation skips the repeated evidence
pass but still routes files and applies available verified indexing.

## 6. Edit File and Edit Album

```mermaid
flowchart LR
    subgraph EditFile["Edit File"]
        One["One selected media file"] --> Action{"Metadata, artwork, trim, or redownload"}
        Action -->|Trim| LocalRange["Song metadata local trim range"]
        Action -->|Redownload| DownloadRange["File operation download range"]
        LocalRange --> OneWrite
        DownloadRange --> OneWrite
        Action -->|Metadata| OneWrite["Write one collision-safe output or replace source"]
    end

    subgraph EditAlbum["Edit Album"]
        Folder["Selected album folder"] --> Inspect["Inspect all supported files"]
        Inspect --> Shared["Album, year, optional shared track-artist override"]
        Shared --> Stage["Stage metadata changes and new names"]
        Stage --> Apply["Apply across every file"]
        Apply --> Rollback{"Any failure?"}
        Rollback -->|Yes| Restore["Restore original paths/tags"]
        Rollback -->|No| Done["Return updated album folder"]
    end
```

Edit Album changes the normal artist tag only when a shared **Artist(s) override** is
entered; it never changes the separate compilation-only album-artist value. When the
override is blank, every file keeps its own track artist, making soundtrack and compilation
folders safe to update. Titles and track numbers are preserved while filenames are rebuilt
from the title, new shared album identity, and each resulting track artist.

## 7. Media Library scan, filter, and browse

```mermaid
sequenceDiagram
    actor User
    participant Page as MediaLibraryPage
    participant Scanner as LibraryScanner
    participant Search as LibrarySearchWorker
    participant Albums as AlbumGridListWidget
    participant Player as QMediaPlayer

    User->>Page: Add folder or select Refresh
    Page->>Scanner: Scan configured roots
    Scanner-->>Page: LibraryItem list
    Page->>Page: Preserve selection and rebuild filters
    User->>Page: Enter text/year or select artist
    Page->>Search: Debounced query and current artist selection
    Search-->>Page: Matches, suggestions, available artists
    Page->>Albums: Render albums from the same filtered set
    User->>Albums: Wheel one notch
    Albums->>Albums: Advance exactly one visual row
    User->>Page: Play/queue a track or album
    Page->>Player: Load queue item
    Player-->>Page: Position, duration, state, metadata
    User->>Page: Add track, album, or artist to playlist
    Page->>Page: Compare exact paths with saved playlist
    alt Duplicate path exists
        Page-->>User: Skip duplicates or add anyway
    end
    Page->>Page: Persist ordered path links in settings
```

### LAN phone synchronization

```mermaid
sequenceDiagram
    actor Phone
    participant Server as RemoteMediaServer
    participant Page as MediaLibraryPage (Qt thread)
    participant Store as QSettings / media files

    Phone->>Server: POST PIN
    Server-->>Phone: Session token
    Phone->>Server: Poll authenticated state revision
    Server-->>Phone: Tracks, albums, playlists, queue, curator
    Phone->>Server: Reorder/add/remove/play/queue/curate action
    Server->>Page: Emit remote_action_requested
    Page->>Store: Use existing desktop mutation path
    Page->>Server: Publish revised path-hiding snapshot
    Phone->>Server: Poll next revision
    Server-->>Phone: Synchronized state
```

Media streaming accepts byte ranges for browser seeking but only resolves opaque IDs in
the current server allowlist. Search filters execute in each phone client; curator actions
execute through the desktop's configured AI and evidence stack.

## 8. Smart Library Curator multi-agent flow

```mermaid
flowchart TD
    Query["Natural-language request"] --> Literal["Recover literal artists and count"]
    Literal --> Planner["Agno query planner"]
    Planner --> Recover["Deterministically recover omitted constraints"]
    Recover --> Local["Filter bounded indexed local candidates"]
    Local --> Evidence["Parallel bounded catalog and DuckDuckGo evidence"]
    Evidence --> Promote["Promote evidence-backed language constraints"]
    Promote --> Verify["Agno semantic verifier"]
    Verify --> Corroborate{"Every requested filter independently corroborated?"}
    Corroborate -->|No| Reject["Reject candidate"]
    Corroborate -->|Yes| Rank["Agno curator ranking"]
    Rank --> Existing["Return existing local file paths only"]
    Existing --> Mix{"Start mix?"}
    Mix -->|No| Results["Display results"]
    Mix -->|Yes| Continue["Second pass for verified same-language continuation"]
    Continue --> Queue["Append deduplicated local tracks to queue"]
```

Language cannot be inferred from artist nationality. Mood, activity, style, energy, and
tempo require an exact supporting evidence phrase (or an independently adjudicated close
musical synonym) for the same recording.

## 9. AI provider fallback

```mermaid
sequenceDiagram
    participant Caller as Service agent
    participant Factory as agno_provider
    participant Primary as Selected provider
    participant Ollama as Local Ollama
    participant Static as Deterministic path

    Caller->>Factory: Structured request + schema
    Factory->>Primary: Agent.run
    alt Primary succeeds
        Primary-->>Factory: Structured content
        Factory-->>Caller: Validated model object
    else Primary missing/fails
        Factory->>Ollama: Direct Agent.run with 16K context cap
        alt Ollama succeeds
            Ollama-->>Caller: Validated structured content
        else Ollama fails
            Factory-->>Caller: Provider exception
            Caller->>Static: Evidence and deterministic fallback
        end
    end
```

## 10. Playback queue state

```mermaid
stateDiagram-v2
    [*] --> Empty
    Empty --> Loaded: play selection / start mix
    Loaded --> Playing: play
    Playing --> Paused: pause
    Paused --> Playing: resume
    Playing --> Loaded: next / previous
    Playing --> Loaded: media ended and queue continues
    Loaded --> Stopped: stop
    Paused --> Stopped: stop
    Stopped --> Playing: play
    Loaded --> Empty: clear queue
    Stopped --> Empty: clear queue
```

Shuffle stores an authoritative source order so it can be disabled without losing queue
identity. Repeat supports off, one, and all. Queue reordering preserves the current track
by path rather than by row number.

## 11. Cancellation and failure handling

```mermaid
flowchart TD
    Running["Operation running"] --> Event{"Cancellation or error?"}
    Event -->|Cancel| Token["Set CancellationToken"]
    Token --> StopWaits["Interrupt delays and prevent new units"]
    StopWaits --> Join["Cancel queued futures and join active workers"]
    Join --> Cancelled["Emit cancelled"]
    Event -->|File locked| Prompt["Emit file-in-use prompt"]
    Prompt --> Retry{"Retry or cancel?"}
    Retry -->|Retry| Running
    Retry -->|Cancel| Token
    Event -->|Transient safe failure| Backoff["Bounded operation/item retry"]
    Backoff --> Running
    Event -->|Terminal failure| Failed["Emit message + traceback; retain logs"]
    Event -->|No| Complete["Emit OperationSummary"]
```

## 12. Build and release flow

```mermaid
flowchart TD
    PR["Ready pull request"] --> Quality["Ubuntu tests and Ruff gate"]
    Quality --> Review["Human/Sourcery review"]
    Review --> Merge["Merge to main"]
    Merge --> MainQuality["Post-merge quality job"]
    MainQuality --> Version["Choose semantic version from commit intent"]
    Version --> Win["Windows x64 installer"]
    Version --> Linux["Linux x64 installer"]
    Version --> Mac["macOS Apple-silicon DMG"]
    Version --> Python["Wheel and Raspberry Pi archive"]
    Win --> Release["Changelog, release commit, tag"]
    Linux --> Release
    Mac --> Release
    Python --> Release
    Release --> Checksums["SHA256SUMS.txt"]
    Checksums --> GitHub["GitHub Release and artifacts"]
```

Pull requests run only the quality gate. A successful main run selects the next version,
builds all targets, promotes curated `Unreleased` changelog notes, creates a release
commit/tag, and publishes one release with checksums.
