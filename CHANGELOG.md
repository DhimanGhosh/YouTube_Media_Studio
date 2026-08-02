# Changelog

All notable changes to YouTube Media Studio are recorded here. Automated releases add commit subjects and abbreviated commit hashes to the next version section.

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
