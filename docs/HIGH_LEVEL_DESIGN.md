# High-level design

## 1. Purpose and scope

YouTube Media Studio is a cross-platform Python application for authorized media
acquisition, splitting, tagging, editing, organization, search, and local playback. It
ships as a PyQt6 desktop application and as command-line tools. Media work is performed
by a shared service layer using yt-dlp, FFmpeg/FFprobe, Mutagen, public catalog sources,
and optional Agno-coordinated AI providers.

## 2. System context

```mermaid
flowchart LR
    User["User"] --> Desktop["Desktop application"]
    User --> CLI["CLI commands and JSON jobs"]

    Desktop --> Core["YouTube Media Studio service layer"]
    CLI --> Core

    Core --> LocalMedia["Local media library and output folders"]
    Core --> Settings["Per-user settings, trackers, diagnostics"]
    Core --> YouTube["YouTube via yt-dlp"]
    Core --> Catalogs["Wikipedia, Apple catalog, SerpApi, DuckDuckGo evidence"]
    Core --> Providers["Agno AI provider chain"]
    Core --> Tools["FFmpeg, FFprobe, Deno"]

    Providers --> Hosted["Hosted model APIs"]
    Providers --> Ollama["Local Ollama server"]
```

### Actors and external systems

| Actor/system | Responsibility |
| --- | --- |
| User | Configures jobs, reviews results, starts/cancels work, and owns source/output rights |
| YouTube/yt-dlp | Source metadata, stream discovery, and permitted media transfer |
| FFmpeg/FFprobe | Transcoding, splitting, trimming, probing, and container work |
| Catalog/evidence services | Album, song, year, artwork, language, mood, style, and identity evidence |
| Hosted AI providers | Optional structured planning and verification through Agno |
| Ollama | Optional local primary model or fallback model |
| GitHub Actions | Tests, packages, versions, tags, and publishes releases |

## 3. Logical containers

```mermaid
flowchart TB
    subgraph Interfaces
        Launcher["launcher.py"]
        GUI["PyQt6 GUI"]
        Phone["Responsive LAN browser client"]
        Commands["CLI modules"]
    end

    subgraph Application
        Dispatch["GUI operation dispatch"]
        Workers["QThread operation workers"]
        Domain["Domain models"]
        Loaders["JSON loaders and validation"]
    end

    subgraph Services
        Downloads["Audio/video downloaders"]
        Splitters["Album/jukebox splitters"]
        Metadata["Tagging, editing, enrichment, consolidation"]
        Library["Index, filter, curate, play queue"]
        Remote["Authenticated LAN media server"]
        AI["Agno provider and safety agents"]
    end

    subgraph Infrastructure
        Storage["QSettings and application data"]
        Files["File access, naming, cancellation"]
        Runtime["yt-dlp, FFmpeg, FFprobe, Deno"]
        Internet["YouTube and public evidence"]
    end

    Launcher --> GUI
    Launcher --> Commands
    Phone --> Remote --> Library
    GUI --> Workers --> Dispatch
    Commands --> Services
    Dispatch --> Services
    Loaders --> Domain
    Services --> Domain
    Services --> Files
    Services --> Runtime
    Services --> Internet
    GUI --> Storage
    AI --> Internet
```

## 4. Desktop composition

The desktop shell owns navigation, global settings, session history, live logs, and
operation lifecycle. Task pages collect inputs and turn them into plain parameter
dictionaries. `OperationWorker` executes the selected operation outside the GUI thread,
captures service output, and emits structured progress, completion, failure, cancellation,
and file-in-use signals.

```mermaid
flowchart LR
    Pages["Task-focused pages"] --> MainWindow["MainWindow"]
    MainWindow --> Worker["OperationWorker in QThread"]
    Worker --> Dispatch["execute_operation"]
    Dispatch --> Service["Selected service"]
    Service --> Result["OperationSummary"]
    Result --> Worker
    Worker --> Logs["Live Logs and progress"]
    Worker --> Dashboard["Session history"]
    Worker --> Refresh["Media Library refresh"]
```

The Media Library is a persistent page with its own background scanner, search worker,
recommendation worker, path-based playlist store, queue, and `QMediaPlayer`. Playback
continues while the user visits other pages. While the desktop runs, its embedded
`ThreadingHTTPServer` publishes a path-hiding snapshot to PIN-authenticated browsers on
the private LAN. Phone actions cross a Qt signal boundary before mutating the desktop
queue, playlists, or curator, and clients poll snapshot revisions for bidirectional sync.

## 5. Deployment view

```mermaid
flowchart TB
    subgraph SourceRun["Source checkout"]
        Python["Python 3.11+"]
        UV["uv environment"]
        Source["src package"]
    end

    subgraph DesktopRelease["Desktop release"]
        Frozen["PyInstaller application"]
        Bundled["Bundled FFmpeg, FFprobe, Deno"]
        Installer["Windows Setup / Linux installer / macOS DMG"]
    end

    subgraph CLIRelease["Automation release"]
        Wheel["Python wheel"]
        Pi["Raspberry Pi CLI archive"]
    end

    Source --> Frozen --> Installer
    Source --> Wheel
    Source --> Pi
    Bundled --> Frozen
```

Desktop builds run natively on Windows, Ubuntu, and Apple-silicon macOS. The Raspberry Pi
archive intentionally excludes PyQt6 and expects compatible media runtimes on the device.

## 6. Data architecture

```mermaid
flowchart LR
    Inputs["Form data or JSON job"] --> Models["Normalized immutable domain models"]
    Models --> Services["Download, split, edit, enrich"]
    Services --> Media["Media files and tags"]
    Services --> Reports["JSON result reports and logs"]

    SettingsUI["Global Settings and workspace"] --> Ini["settings.ini via QSettings"]
    Enrichment["Metadata verifier"] --> Tracker["album_enrichment_tracker.json"]
    Crash["Crash reporter"] --> Diagnostics["crash_reports/"]
    Pointer["Storage location pointer"] --> AppData["Selected application-data folder"]
```

Key ownership rules:

- Batch JSON or editor rows own per-entry source URLs, metadata, resolution, download
  enablement, and start/end timestamps.
- `settings.ini` owns UI defaults, provider-specific credentials, workspace drafts,
  library folders, volume, shuffle, repeat mode, and window state.
- Media tags remain the source of truth for library title, album, artist, year, artwork,
  track number, and duration display.
- The enrichment tracker records completion by verifier policy so an older or weaker
  decision cannot incorrectly suppress a newer validation pass.

## 7. AI and evidence architecture

```mermaid
flowchart LR
    Request["Operation or curator request"] --> Planner["Structured Agno agent"]
    Planner --> Primary{"Configured provider available?"}
    Primary -->|Yes| Hosted["Selected hosted/local model"]
    Primary -->|No| Fallback["Configured Ollama model"]
    Fallback -->|Unavailable| Static["Deterministic fallback"]

    Hosted --> Proposed["Structured proposal"]
    Fallback --> Proposed
    Proposed --> Evidence["Catalog and bounded web evidence"]
    Static --> Evidence
    Evidence --> Gate{"Identity and semantic gates pass?"}
    Gate -->|Yes| Apply["Rank result or apply metadata"]
    Gate -->|No| Review["Reject, skip, or request review"]
```

Provider definitions are centralized. Ollama, NVIDIA NIM, OpenAI, Anthropic, Google
Gemini, Groq, Hugging Face Inference, OpenRouter, OpenCode Zen, and custom
OpenAI-compatible endpoints resolve into Agno model objects. Hosted failure triggers one
direct local retry. Constrained curator filters fail closed when neither model nor
independent evidence can safely establish the requested property.

## 8. Security, privacy, and safety boundaries

- API keys are password-masked, stored per provider in local settings, omitted from logs,
  and removable by saving an explicitly blank value.
- The curator sends bounded metadata/evidence, not media file contents, to model APIs.
- Local Ollama inference stays local, while evidence lookups may still use the internet.
- File mutation services validate paths and formats, use collision-safe names, preserve
  tags where appropriate, and surface file locks rather than forcibly overwriting files.
- Cancellation tokens coordinate worker pools and subprocess termination.
- Download authorization remains the user's responsibility.

## 9. Quality attributes

| Attribute | Design response |
| --- | --- |
| Responsiveness | QThread workers, thread pools, debounced library search, asynchronous artwork/evidence |
| Reliability | Bounded retries, completion reports, cancellation, skip-existing behavior, transactional replacement |
| Correctness | Immutable normalized models, input validation, evidence gates, conservative fallbacks |
| Portability | Python service layer, runtime discovery, native platform packaging |
| Maintainability | Layered packages, central operation dispatch, provider registry, focused tests |
| Observability | Live logs, progress phases, result summaries, crash diagnostics, CI checks |
| Scalability | Bounded workers/candidates/evidence calls and lazy library rendering |

## 10. Major constraints

- Source-site changes can require yt-dlp updates.
- Desktop packaging is native per operating system.
- Public evidence can be incomplete; safety rules prefer no result over a fabricated one.
- Ollama CPU/GPU placement is controlled by the Ollama runtime and available VRAM; the
  app limits context allocation but does not override the runtime's device scheduler.
- GUI state is local to a user profile and is not a multi-user database.
