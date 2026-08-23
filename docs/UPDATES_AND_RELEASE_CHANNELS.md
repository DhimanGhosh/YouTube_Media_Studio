# Updates, release channels, and sign-in

## Release policy

- `main` contains the code and documentation for the latest public release. Beta-only code is never merged into it.
- `release/2.x` is the maintained stable 2.x release line. Its GitHub releases are public, non-prerelease `2.x.x` versions.
- `release/3.x` contains experimental built-in-AI work. Every release from this branch uses an explicit beta version such as `3.0.0-beta.1` and is published as a GitHub prerelease.
- Shared fixes are deliberately forward-ported between maintained release lines. When a 3.x beta becomes public, that public code and its documentation are merged into `main`.

The desktop updater follows the stable channel by default. Stable users do not see `3.x` prereleases. A user can explicitly enable **Include 3.x beta releases** in **File → Settings… → Software updates**. Turning that option off immediately returns update selection to the newest non-prerelease release. **Help → Check for Updates…** provides the same check directly from the application menu bar.

## OTA update design

The application queries the repository's public GitHub Releases API in a background thread, compares versions, selects the installer for the current operating system, and asks before downloading or launching anything. On Windows, the downloaded NSIS installer closes the old executable and replaces it after the application exits. User settings, playlists, and media are stored separately and are not removed by an upgrade or uninstall.

## Google sign-in analysis

Google sign-in is **not required for application updates**. Public GitHub release metadata and release assets can be read without a GitHub or Google account. Adding Google OAuth to the updater would add credentials, consent screens, token storage, privacy review, and failure modes without improving update delivery.

Google sign-in could be useful in a separate, opt-in feature for:

- synchronizing playlists or preferences through a user-owned cloud service;
- accessing private Google Drive files after explicit Drive consent;
- authenticated YouTube Data API actions such as reading the signed-in user's private playlists.

Those uses require narrowly scoped OAuth permissions and a published privacy policy. They should not be coupled to downloads, local media tools, playback, or updates. No Google account is required by the current implementation.
