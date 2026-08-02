# Changelog

All notable changes to YouTube Media Studio are recorded here. Automated releases
promote curated `Unreleased` notes when present; otherwise they add commit subjects and
abbreviated hashes to the next version section.

## [Unreleased]

### Fixed

- Record automated release dates in the project timezone (`Asia/Kolkata`) rather than
  the GitHub runner's UTC calendar date.
- Correct the 2.0.x changelog dates to match their August 3 release times in India.

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

## [2.0.5] - 2026-08-03

### Changed

- Automated release with no new commit messages.

## [2.0.4] - 2026-08-03

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

## [2.0.3] - 2026-08-03

### Changed

- Automated release with no new commit messages.
## [2.0.2] - 2026-08-03

### Changed

- Automated release with no new commit messages.
## [2.0.1] - 2026-08-03

### Changed

- Automated release with no new commit messages.
## [2.0.0] - 2026-08-03

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
