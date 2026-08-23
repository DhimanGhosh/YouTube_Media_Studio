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

The existing OTA updater therefore remains account-free:

1. About 2.5 seconds after startup, a background worker queries the public GitHub
   Releases API. The same check is available from **Help → Check for Updates…** and
   **File → Settings… → Software updates**.
2. The stable channel ignores prereleases. A user must explicitly opt into 3.x beta
   releases.
3. When a newer compatible release is found, the app shows its version and notes and
   asks before downloading anything.
4. The platform installer is downloaded to an isolated temporary directory and its
   SHA-256 digest must match the release's `SHA256SUMS.txt` entry.
5. The app asks again before launching the verified installer and closing itself. It
   never performs a silent install.

Google sign-in is implemented as a separate, optional **Connected services** feature
for:

- importing private YouTube playlists, subscriptions, or the signed-in channel's own
  uploads with the read-only YouTube scope;
- creating or editing YouTube playlists only after the user explicitly enables a
  playlist-management feature;
- backing up settings and playlists to the application-specific Drive folder; or
- exporting/importing a user-selected library or playlist file through the narrow
  `drive.file` scope.

### Google Cloud Profile implementation

1. The release publisher creates a Google Cloud desktop OAuth client, enables Google
   Drive and YouTube Data API v3, and supplies its downloaded desktop-client JSON. A
   source or development build can select that file under **File → Settings… →
   Connected services**.
2. **Connect Google account** opens the system browser and uses Authorization Code with
   PKCE, a random state value, and a temporary loopback redirect.
3. The app requests `openid`, `email`, `drive.appdata`, and `youtube.readonly`. It cannot
   browse the user's normal Drive or modify YouTube data.
4. The refresh token is stored in the operating-system credential vault, never in
   `settings.ini`, logs, crash reports, a cloud profile, or the repository.
5. The versioned cloud profile contains an allow-listed set of portable preferences and
   playlist track identities (title, artist, album, and year). It deliberately excludes
   local file/folder paths, media files, API keys, OAuth credentials, and crash reports.
6. After connection, an existing profile is restored. Playlist identities are matched
   to the local library on that machine; missing media is reported and is not downloaded.
   With automatic sync enabled, later playlist changes are backed up after a short
   debounce. Manual **Back up now** and **Restore** remain available.
7. **Import YouTube playlist** reads the signed-in user's private playlists and adds
   tracks already found in the local library. It does not download absent tracks.
8. **Disconnect** revokes and removes the stored token. Downloading, local playback,
   metadata tools, and OTA updates remain functional without a Google account.

Google recommends PKCE for desktop OAuth clients. The YouTube Data API requires OAuth
for private user data, while public GitHub API data can be read anonymously. Drive's
narrow `drive.appdata` scope avoids granting access to the user's normal Drive files.

References: [Google YouTube OAuth](https://developers.google.com/youtube/v3/guides/authentication),
[Google OAuth security practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices),
[Google Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth),
and [GitHub REST API rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api).
