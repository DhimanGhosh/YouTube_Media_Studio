# Changelog

All notable changes to YouTube Media Studio are recorded here. Automated releases
promote curated `Unreleased` notes when present; otherwise they add commit subjects and
abbreviated hashes to the next version section.

## [2.0.4] - 2026-08-02

### Added

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

- Treat SerpApi as an optional third verification source for both deterministic and
  AI-assisted enrichment while continuing to protect populated local album metadata.
- Use paid Google searches only as a fallback when the Apple catalog did not produce a
  usable album, avoiding unnecessary SerpApi quota consumption.

### Fixed

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
