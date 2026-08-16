# Changelog

All notable changes to YouTube Media Studio are recorded here. Automated releases
promote curated `Unreleased` notes when present; otherwise they add commit subjects and
abbreviated hashes to the next version section.

## [2.11.2] - 2026-08-17

### Fixed

- fix: refine media library playback controls (#35) (`c0c9d4c`)
## [2.11.1] - 2026-08-17

### Changed

- test: organize suite by application area (#34) (`bb98f7c`)
## [2.11.0] - 2026-08-17

### Added

- feat: enhance Media Library organization and playback (#33) (`a11b1b6`)
## [2.10.2] - 2026-08-17

### Fixed

- fix: stop phone playback on disconnect (`90af2c3`)
## [2.10.1] - 2026-08-16

### Fixed

- fix: add persistent phone access switch (`9f33ccb`)
## [2.10.0] - 2026-08-16

### Added

- feat: add LAN mobile Media Library (`9e32784`)
## [2.9.6] - 2026-08-16

### Changed

- Improve playlist ordering and media filters (#29) (`960e3df`)
## [2.9.5] - 2026-08-13

### Changed

- Merge pull request #28 from DhimanGhosh/codex/fix-frozen-queue-overlay (`64f711a`)
- Fix frozen queue row after drag deletion (`0ea1c22`)
## [2.9.4] - 2026-08-12

### Fixed

- fix(package): add MIT and project metadata (`ba47599`)

### Changed

- Merge pull request #27 from DhimanGhosh/codex/package-metadata (`02fdef5`)
## [2.9.3] - 2026-08-12

### Fixed

- fix(package): include Python library guide in sdist (`ff8ea05`)

### Changed

- Merge pull request #26 from DhimanGhosh/codex/package-python-library-guide (`0dddc6a`)
## [2.9.2] - 2026-08-12

### Changed

- Merge pull request #25 from DhimanGhosh/codex/python-library-guide (`cf51aa0`)
- docs: add complete Python library guide (`d7c06d7`)
## [2.9.1] - 2026-08-12

### Fixed

- fix: version wheel filenames and verify upgrades (`3e13586`)

### Changed

- Merge pull request #24 from DhimanGhosh/codex/versioned-wheel-upgrades (`f13b5c1`)
## [2.9.0] - 2026-08-12

### Added

- feat: expose GUI workflows as a Python API (`4b058c5`)

### Changed

- Merge pull request #23 from DhimanGhosh/codex/python-library-api (`0d06a49`)
## [2.8.0] - 2026-08-12

### Added

- feat: run workspace jobs concurrently (#22) (`a0586b8`)
## [2.7.2] - 2026-08-12

### Fixed

- fix: preserve per-track artists in album editor (#21) (`2275401`)
## [2.7.1] - 2026-08-12

### Fixed

- fix: contain cropped video within playback area (#20) (`a641a0d`)
## [2.7.0] - 2026-08-12

### Added

- feat: add advanced video browsing and playback controls (#19) (`3ee400c`)
## [2.6.0] - 2026-08-12

### Added

- feat: separate media modes and add fullscreen video (#18) (`08b6fb6`)
## [2.5.4] - 2026-08-11

### Fixed

- fix: make library refresh and outputs reliable (#17) (`516641a`)
## [2.5.3] - 2026-08-11

### Changed

- Let the configured AI model semantically decide which saved playlist themes match a
  curator request, without fixed keyword mappings, and admit their member tracks as
  personal taste matches even when public catalogs do not carry the same descriptive tag.

## [2.5.2] - 2026-08-11

### Changed

- Personalize Smart Library Curator with playlist taste (#15) (`e92cfcd`)
## [2.5.1] - 2026-08-11

### Added

- Let Smart Library Curator learn bounded taste context from saved playlist names and
  their indexed tracks when ranking local suggestions or preparing a YouTube search.
- Add persistent path-based Media Library playlists with create, rename, delete,
  play, queue, and track removal controls; add tracks from track, album, artist, and
  playlist context menus or the main track/album action rows.
- Warn when a destination playlist already contains a selected file and let the user
  either skip duplicate paths while adding the remaining selection or add them anyway.

### Changed

- Give Edit File separate download-range and local-file trim-range fields, with an
  explicit offline trim action and action-specific controls so the ranges cannot mix.
- Present the desktop interface as one native window by removing the redundant inner
  title header and rounded outer inset while preserving native all-edge resizing.

## [2.5.0] - 2026-08-11

### Added

- Add album-wide artwork replacement and removal controls to Edit Album, including
  embedded-artwork coverage reporting and preserved-artwork behavior when unchanged.

### Fixed

- Restore native edge and corner window resizing on Windows, macOS, and Linux, and pause
  animated background effects during resize to prevent Windows flicker and UI stalls.

## [2.4.3] - 2026-08-11

### Added

- Add a public privacy policy covering local storage, credentials, optional diagnostics,
  third-party network services, retention, deletion, and user controls.

## [2.4.2] - 2026-08-11

### Fixed

- Keep overlapping background jobs from restoring output streams owned by deleted Qt
  workers, preventing album auto-fill track extraction from failing with an
  `OperationWorker has been deleted` error.

## [2.4.1] - 2026-08-10

### Added

- Add full-project high-level and low-level architecture documentation with Mermaid
  context, class, sequence, state, flowchart, deployment, and release views.

### Changed

- Move Audio Downloader and Video Downloader start/end timestamps into each individual
  batch entry so every song or video can select a different source interval.
- Make the Media Library album wheel advance one visual row per notch and let the main
  search field consume available width while the remaining controls fit their labels.

## [2.4.0] - 2026-08-10

### Added

- Add optional start and end timestamps to Audio Downloader and Video Downloader,
  using one validated native download range for both video and optional MP3 output.
- Add compact removable Media Library folder chips, explicit track-filter navigation,
  a clear-search action, and a resizable track/album browser with more album space.

### Changed

- Move Edit File trim timestamps into Song metadata, collapse Smart Library Curator
  until needed, and move library refresh to the page header.
- Remove the Duplicate links tab from desktop Utilities while preserving the existing
  command-line compatibility tool.

### Fixed

- Let the curator accept a semantic synonym only when its verifier cites an exact phrase
  from that song's bounded public evidence, avoiding false empty results for requests
  such as `Arijit sad Bengali` without weakening strict language validation.
- Rank query tokens as whole words, give every semantic candidate the same bounded
  evidence opportunity, and describe an empty result as inconclusive verification
  instead of claiming the local library contains no matching track.
- Preserve an artist selection when a user selects or plays a track, and apply that
  artist filter to both the track table and album browser until explicitly cleared.
- Skip the macOS symlink-only DMG assertion on Windows, where creating that alias
  requires an unrelated elevated developer privilege.

## [2.3.3] - 2026-08-10

### Fixed

- Use Agno's bounded DuckDuckGo evidence tool to promote recovered language requests
  into strict catalog-backed validation, reject conflicting language results, and
  collapse duplicate recordings indexed at different file paths.

## [2.3.2] - 2026-08-10

### Fixed

- Recover meaningful natural-language constraints omitted by the Smart Library Curator
  planning model and require independent evidence for them, preventing artist-only
  results from bypassing requested language, mood, tempo, genre, or activity filters.

## [2.3.1] - 2026-08-10

### Added

- Add a visible Smart Library Curator result-count control, natural-language count
  overrides, and Start mix playback that continues with verified same-language tracks.

### Changed

- Simplify Global Settings section labels and remove the generic Runtime requirements
  accordion from that page.
- Make Edit Album replace album, year, and the normal track Artist(s) across every song
  in the selected folder, then safely rebuild filenames from the preserved titles and
  new shared identity.

### Fixed

- Display and invoke the saved hosted provider and model in Smart Library Curator instead
  of incorrectly reporting the Ollama fallback identity.
- Reject curator language matches that conflict with or lack independent catalog/web
  evidence, preventing Hindi tracks from being labeled as Bengali by model assertion.
- Require the same independent corroboration for requested mood, activity, style, energy,
  and tempo filters, preventing upbeat tracks from being asserted as slow and suppressing
  unrequested model-invented traits from result explanations.
- Pass the configured library suggestion count into the curator instead of silently using
  its internal eight-result default.
- Replace Qt mnemonic ampersands in Global Settings titles so section names no longer
  render with stray underscores.

## [2.3.0] - 2026-08-10

### Added

- Add Agno-powered Smart Library Curator, which decomposes natural-language requests,
  searches only the indexed local library, verifies semantic music qualities with
  bounded public evidence, and ranks validated local tracks without automatic YouTube
  redirection.
- Add selectable Ollama, NVIDIA NIM, OpenAI, Anthropic, Google Gemini, Groq, Hugging
  Face Inference, OpenRouter, OpenCode Zen, and custom OpenAI-compatible providers with
  independent saved credentials and local Ollama fallback.
- Add Edit Album for album-wide metadata editing, including album-level Media Library
  context actions.

### Changed

- Organize Global Settings into focused persistent collapsible sections with clearer
  save/reset actions and provider-specific controls.
- Let Album Consolidator skip a repeated enrichment pass while still routing files and
  applying verified track indexing.

### Fixed

- Preserve an explicitly cleared hosted-provider key after save and restart.
- Use the configured local Ollama model through Agno and retry it directly when the
  selected hosted model cannot complete a request.
- Cap application Ollama requests at a 16K context window so models do not allocate an
  unused 262K KV cache and spill inference onto CPU on common GPUs.

## [2.2.4] - 2026-08-10

### Changed

- fix Ollama preflight timeout (#2) (`d20a54a`)
## [2.2.3] - 2026-08-10

### Changed

- fix AI provider settings and Ollama requests (`7d0512b`)
## [2.2.2] - 2026-08-04

### Fixed

- Prefer verified official YouTube audio jukeboxes during Album Splitter auto-fill,
  including chapter-backed uploads where the track list is not in the description.
- Normalize YouTube radio/playlist URLs to the selected video before extracting tracks,
  skip only out-of-range timestamp rows instead of rejecting the whole list, and keep
  numbered title variants such as `Tum Mile (2)` distinct.
- Log Album Splitter track-extraction failures and album-art preview outcomes in
  Live Logs.

## [2.2.1] - 2026-08-04

### Fixed

- Report Album Splitter auto-fill outcomes in Live Logs and show **No new data** when
  metadata lookup does not update any visible album fields or track rows.

### Documentation

- Document the per-Mac quarantine removal and optional ad-hoc signing commands needed
  to launch unsigned macOS DMG builds without an Apple Developer Program membership.

## [2.2.0] - 2026-08-04

### Added

- Add optional macOS Developer ID signing and Apple notarization for release builds so
  CI can publish Gatekeeper-trusted drag-and-drop DMGs when signing secrets are present.

## [2.1.1] - 2026-08-04

### Fixed

- fix: make macOS DMG drag-and-drop install (`d89b967`)
## [2.1.0] - 2026-08-03

### Added

- Add an optional Windows **Create a desktop shortcut** installer component while
  retaining the standard Start-menu shortcut, preserving the choice during maintenance,
  and removing both shortcuts during uninstall.

### Fixed

- Preserve conventional commits with empty bodies while parsing Git history so scoped
  `feat(...)` commits correctly trigger an automatic minor release.

## [2.0.9] - 2026-08-03

### Changed

- Replace the README banner's approximate waveform/play emblem with the exact packaged
  application logo and identify YouTube Media Studio consistently as AI-powered.
- Explain how hosted NVIDIA NIM, local Ollama, deterministic processing, and the
  separate SerpApi credential help users while preserving evidence-based safety gates.

## [2.0.8] - 2026-08-03

### Fixed

- Search SerpApi with the natural full `Title - Artist` filename context instead of an
  over-constrained keyword query that can suppress Google's song knowledge panel.
- Read nested album and movie cards from SerpApi knowledge-graph results and accept an
  exact title when Google credits at least one artist from a multi-artist filename.

## [2.0.7] - 2026-08-03

### Fixed

- Record automated release dates in the project timezone (`Asia/Kolkata`) rather than
  the GitHub runner's UTC calendar date.
- Correct the 2.0.6 changelog date to match its August 3 release time in India.

## [2.0.6] - 2026-08-03

### Changed

- Switch Setup into maintenance mode when an installed desktop copy is detected, with
  explicit Upgrade, Repair with fresh binaries, and Uninstall choices plus an optional
  application-data removal control.
- For upgrades and repairs, detect the registered location, close only a running process
  with the exact installed executable path, remove old program files and operating-system
  registration, then install and register the selected release automatically.
- Preserve settings, history, and application data during automatic upgrades.

### Removed

- Remove the Intel macOS installer from GitHub Actions, release artifacts, native build
  tooling, workflow tests, download instructions, and release-pipeline documentation.
  macOS desktop releases now target Apple silicon only, and the release tool rejects
  accidental Intel macOS builds.

### Fixed

- Prevent Windows upgrades from failing with `WinError 5: Access is denied` when the
  installed YouTube Media Studio executable is still running.
- Keep the Windows installer simulation isolated from the host operating system so the
  cross-platform release gate does not import Windows-only registry modules on Linux.

## [2.0.5] - 2026-08-02

### Changed

- Automated release with no new commit messages.

## [2.0.4] - 2026-08-02

### Added

- Display the installed application version persistently beneath the desktop sidebar
  and expose the same value through Qt application metadata and the native window title.
- Add an optional password-masked SerpApi key field to Global Settings so each user
  can supply their own Google Search API access without placing the credential in
  operation parameters or logs.
- Add conservative SerpApi song-metadata evidence for Album Enricher when the built-in
  Wikipedia and Apple catalog lookup cannot identify a usable album.
- Extract exact title, artist, film/album, and release-year relationships from
  structured Google knowledge data or independently agreeing search results, then use
  the resolved album with the existing artwork lookup.
- Add authenticated Google Images artwork fallback through SerpApi, accepting only
  safe square original images when the Apple catalog has no suitable cover.

### Changed

- Resolve the runtime version from installed package metadata with packaged and source
  fallbacks, and include distribution metadata in PyInstaller desktop builds so the GUI
  always follows the version selected by GitHub Actions.
- Centralize production identity values—including product, organization, distribution,
  executable, CLI, desktop, and Windows registration names—and derive every outbound
  service user-agent from the same runtime version instead of a hard-coded `2.0` value.
- Treat SerpApi as an optional third verification source for both deterministic and
  AI-assisted enrichment while continuing to protect populated local album metadata.
- Use paid Google searches only as a fallback when the Apple catalog did not produce a
  usable album, avoiding unnecessary SerpApi quota consumption.

### Fixed

- Remove the Media Library's hidden 250-row display cap so every scanned song and
  video remains visible, sortable, selectable, queueable, and playable.
- Promote curated `Unreleased` notes into the automatically selected release version
  so detailed user-facing changes are not left behind when GitHub Actions publishes a
  successful `main` build.

### Security

- Mask the SerpApi credential in the GUI and suppress request URLs and exception text
  that could expose the API key when a SerpApi request fails.

## [2.0.3] - 2026-08-02

### Changed

- Automated release with no new commit messages.
## [2.0.2] - 2026-08-02

### Changed

- Automated release with no new commit messages.
## [2.0.1] - 2026-08-02

### Changed

- Automated release with no new commit messages.
## [2.0.0] - 2026-08-02

### Added

- Native graphical installers for Windows, macOS, and Linux with an optional CLI component.
- Platform-integrated uninstallers for Windows Installed Apps, macOS, and Linux application menus.
- A CLI-only installer archive for 64-bit Raspberry Pi OS.
- A `youtube-media-studio-uninstall` removal command for Raspberry Pi installations.
- Native cross-platform build, test, checksum, and GitHub Release automation.

### Changed

- Limited the product to desktop GUI/CLI and Raspberry Pi CLI operation.
- Replaced the project guide with clean installation, development, build, and release instructions.
- Made Windows Start-menu shortcut creation robust for paths containing spaces.
- Remove private installer staging folders from `dist/` after a successful build.

### Fixed

- Allow year-qualified album folders to repair embedded album years and legacy
  multi-artist separators even when external metadata evidence is unavailable.

### Removed

- Non-desktop interfaces, services, dependencies, assets, tests, build tooling, and CI jobs.
- The Windows Linux-compatibility-layer build fallback.
