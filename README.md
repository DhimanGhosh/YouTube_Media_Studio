# YouTube Media Studio

YouTube Media Studio is a desktop media toolkit with a PyQt6 GUI and a complete command-line interface. It downloads audio and video, writes metadata and cover art, splits albums and jukeboxes, finds duplicate links, normalizes artist names, and manages a local media library.

The supported product surfaces are:

| Platform | GUI | CLI | Release artifact |
| --- | --- | --- | --- |
| Windows 10/11 (x64) | Yes | Optional installer component | Setup `.exe` |
| macOS (Intel and Apple silicon) | Yes | Optional installer component | Installer `.dmg` |
| Desktop Linux (x64) | Yes | Optional installer component | Graphical `.run` installer |
| Raspberry Pi OS (64-bit) | No | Yes | CLI installer `.tar.gz` |

Desktop installers contain Python, FFmpeg, FFprobe, Deno, the application, and its Python dependencies. End users do not need a development environment.

## Features

- Download and tag individual audio tracks.
- Download video at a selected resolution.
- Split full-album and compilation videos from timestamps.
- Search for songs, albums, metadata, release years, and artwork.
- Inspect, repair, reorder, and consolidate local media metadata.
- Play audio and video from the desktop library.
- Trim audio and edit batches through the GUI.
- Use the same core workflows through scriptable CLI commands.
- Store application settings and working data in a user-selected folder.

Use downloads only when you have permission and follow the source service's terms and applicable law.

## Install a released desktop build

Download the artifact for your operating system from the GitHub Releases page.

### Windows

1. Download `YouTubeMediaStudio-<version>-windows-amd64-Setup.exe`.
2. Run the setup program.
3. Enable **Install command-line interface and add it to my user PATH** if you want terminal commands.
4. Select **Install**.
5. Open **YouTube Media Studio** from the Start menu.
6. Open a new PowerShell window and run `youtube-media-studio doctor` if you installed the CLI.

The installer is per-user and does not require administrator access.

To uninstall, open **Settings → Apps → Installed apps** or **Control Panel → Programs and Features**, select **YouTube Media Studio**, and choose **Uninstall**. The installed uninstaller removes the GUI, optional CLI, Start-menu shortcut, user `PATH` entry, and Installed Apps registration. Its optional checkbox also removes settings and application data.

### macOS

1. Download the DMG matching the Mac architecture.
2. Open the DMG and launch `YouTubeMediaStudio-Setup.app`.
3. Enable **Install command-line interface in ~/.local/bin** if wanted.
4. Select **Install**.
5. Open `~/Applications/YouTubeMediaStudio.app`.

Release artifacts are unsigned until Apple signing is configured. On a test machine, macOS may require **System Settings → Privacy & Security → Open Anyway**. A public release should be signed and notarized before distribution.

If the CLI was selected, ensure this line is present in `~/.zprofile`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Then open a new Terminal and run:

```sh
youtube-media-studio doctor
```

To uninstall, open `~/Applications/Uninstall YouTube Media Studio.app`. It removes the application and optional CLI; enable its data-removal checkbox only when settings and history should also be deleted. If removing manually, move both `YouTubeMediaStudio.app` and `Uninstall YouTube Media Studio.app` to Trash, then remove `~/.local/bin/youtube-media-studio` if the CLI was installed.

### Linux desktop

1. Download `youtube-media-studio-<version>-linux-<architecture>-installer.run`.
2. Make it executable:

   ```sh
   chmod +x youtube-media-studio-*-installer.run
   ```

3. Launch it from the desktop or terminal:

   ```sh
   ./youtube-media-studio-*-installer.run
   ```

4. Enable **Install command-line interface in ~/.local/bin** if wanted.
5. Select **Install**.
6. Launch YouTube Media Studio from the application menu.

If the application does not start on a minimal distribution, install the standard Qt runtime system libraries for that distribution. For Debian/Ubuntu installations this commonly includes OpenGL, XCB, audio, and `libxcb-cursor0` support.

Ensure `~/.local/bin` is in `PATH` before using the optional CLI.

To uninstall, launch **Uninstall YouTube Media Studio** from the application menu. From a terminal, run:

```sh
~/.local/opt/youtube-media-studio/youtube-media-studio-uninstaller --uninstall
```

The uninstaller removes the GUI, optional CLI, and desktop-menu entries. Settings and history are retained unless **Also remove settings, history, and application data** is enabled.

## Install the Raspberry Pi CLI

The Raspberry Pi package intentionally contains no GUI framework.

1. Install 64-bit Raspberry Pi OS and Python 3.11 or newer.
2. Download `youtube-media-tools-<version>-raspi-cli.tar.gz`.
3. Extract and install it:

   ```sh
   tar -xzf youtube-media-tools-*-raspi-cli.tar.gz
   cd youtube-media-tools-*-raspi-cli
   ./install.sh
   ```

4. Add its command directory to `PATH`:

   ```sh
   echo 'export PATH="$HOME/.local/share/youtube-media-tools/bin:$PATH"' >> ~/.profile
   . ~/.profile
   ```

5. Confirm the installation:

   ```sh
   youtube-media-studio --help
   ```

Uninstall from the command line:

```sh
youtube-media-studio-uninstall
```

This removes the managed virtual environment and its uninstall command. User-created media and configuration files outside that environment are not removed.

FFmpeg, FFprobe, and Deno must be available on Raspberry Pi OS. Install FFmpeg with `sudo apt install ffmpeg`; use a Deno package appropriate for the Pi architecture.

## CLI overview

The installed desktop CLI and source-tree launcher use one command:

```text
youtube-media-studio [--data-dir FOLDER] <command> [options]
```

Commands:

| Command | Purpose |
| --- | --- |
| `audio` | Download and tag audio from a JSON job file |
| `video` | Inspect or download video/audio |
| `album` | Split a full-album source |
| `jukebox` | Split a compilation source |
| `duplicates` | Find duplicate links in job files |
| `artists` | Normalize artist names |
| `timestamps` | Convert timestamp text to track JSON |
| `doctor` | Verify FFmpeg, FFprobe, Deno, and yt-dlp |

Examples:

```sh
youtube-media-studio audio config/songs.json
youtube-media-studio video config/videos.json
youtube-media-studio album config/albums.json
youtube-media-studio doctor
```

Use `youtube-media-studio <command> --help` for every option.

## Developer setup

### Prerequisites

- Windows, macOS, or desktop Linux
- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- Git

No Linux compatibility layer is required on Windows. Development and release commands run natively on the host operating system.

### Clone and install

```sh
git clone <your-repository-url>
cd YouTube_Media_Studio
uv sync --extra gui --group dev
```

Run the GUI:

```sh
uv run python run_gui.py
```

Run the unified launcher or a CLI command:

```sh
uv run youtube-media-studio --help
uv run youtube-media-studio doctor
```

### Install or remove the CLI from a cloned repository

For a persistent CLI installation managed by `uv`:

```sh
uv tool install .
youtube-media-studio doctor
```

Remove that installation with:

```sh
uv tool uninstall youtube-media-studio
```

To install both project entry points and PyQt6 into a `uv`-managed tool environment, use:

```sh
uv tool install --with PyQt6 .
youtube-media-studio-gui
```

Remove it with the same `uv tool uninstall youtube-media-studio` command.

On the first source run, the project can download a local FFmpeg/FFprobe runtime through `portable-ffmpeg`. Deno is supplied by the Python dependency declared in `pyproject.toml`.

### Create local job files

Tracked examples live in `config/*.sample.json`. Copy only the files you need; the non-sample names are ignored so personal links, paths, and metadata are not committed.

PowerShell:

```powershell
Copy-Item config/songs.sample.json config/songs.json
Copy-Item config/albums.sample.json config/albums.json
Copy-Item config/videos.sample.json config/videos.json
Copy-Item config/jukeboxes.sample.json config/jukeboxes.json
```

macOS/Linux:

```sh
cp config/songs.sample.json config/songs.json
cp config/albums.sample.json config/albums.json
cp config/videos.sample.json config/videos.json
cp config/jukeboxes.sample.json config/jukeboxes.json
```

## Quality checks

Run the same checks used by CI:

```sh
uv run python tools/release.py check
```

Or run them separately:

```sh
uv run --group dev ruff check src tests tools
uv run --group dev pytest -q
```

## Build releases locally

PyInstaller builds are native: build each desktop installer on its matching operating system. Cross-compilation is not used.

List the release matrix and commands:

```sh
uv run python tools/release.py plan
uv run python tools/release.py build --list-targets
```

Build the installer for the current desktop:

```sh
uv run python tools/release.py build --target current
```

Or use an explicit target on its native host:

```sh
uv run python tools/release.py build --target windows
uv run python tools/release.py build --target macos
uv run python tools/release.py build --target linux
```

Build the Python package and Raspberry Pi CLI archive:

```sh
uv run python tools/release.py build --target wheel
uv run python tools/release.py build --target raspi
```

Artifacts, SHA-256 checksums, and `build-manifest.json` are written to `dist/`.

The desktop build performs four stages:

1. Freeze the PyQt6 application and bundled media runtimes.
2. Freeze a console-enabled CLI with the same bundled runtimes.
3. Freeze and embed a platform-integrated graphical uninstaller.
4. Embed all payloads in a graphical per-user installer. The CLI is copied only when its checkbox is selected.

After cloning, build and launch the native setup program from the command line:

`clean` removes the complete `dist/` directory, including previously built installers. Run it before a fresh release build, not after one you intend to keep.

Windows PowerShell:

```powershell
uv run python tools/release.py clean
uv run python tools/release.py check
uv run python tools/release.py build --target windows
& .\dist\YouTubeMediaStudio-*-Setup.exe
```

Confirm that the Windows setup contains its GUI, CLI, and uninstaller payloads:

```powershell
$setup = Get-Item .\dist\YouTubeMediaStudio-*-Setup.exe
$result = Start-Process -FilePath $setup.FullName -ArgumentList '--check' -Wait -PassThru
$result.ExitCode  # 0 means the payload check passed
Get-FileHash $setup.FullName -Algorithm SHA256
```

macOS:

```sh
uv run python tools/release.py clean
uv run python tools/release.py check
uv run python tools/release.py build --target macos
open dist/youtube-media-studio-*-installer.dmg
shasum -a 256 dist/youtube-media-studio-*-installer.dmg
```

Linux:

```sh
uv run python tools/release.py clean
uv run python tools/release.py check
uv run python tools/release.py build --target linux
chmod +x dist/youtube-media-studio-*-installer.run
./dist/youtube-media-studio-*-installer.run --check
./dist/youtube-media-studio-*-installer.run
sha256sum dist/youtube-media-studio-*-installer.run
```

Raspberry Pi CLI archive (build on any development host, then copy to the Pi):

```sh
uv run python tools/release.py clean
uv run python tools/release.py check
uv run python tools/release.py build --target raspi
tar -tf dist/youtube-media-tools-*-raspi-cli.tar.gz
sha256sum dist/youtube-media-tools-*-raspi-cli.tar.gz
```

Every build also writes `dist/SHA256SUMS.txt` and `dist/build-manifest.json`. The build command performs its own frozen CLI/runtime and embedded-installer payload checks before it reports success.

## GitHub Actions and releases

`.github/workflows/cross-platform-build.yml` provides:

- lint and tests for pull requests;
- full tests before every release build from `main`;
- native Windows, Linux, Intel Mac, and Apple-silicon Mac installer builds;
- Python wheel/source and Raspberry Pi CLI builds;
- artifact checksums;
- automatic version, changelog, Git tag, and GitHub Release creation after every successful `main` build.

Automatic versions follow semantic versioning based on commit messages:

- `type!: ...` or a `BREAKING CHANGE:` commit body increments the major version;
- `feat: ...` increments the minor version;
- all other successful `main` changes increment the patch version.

Use conventional commit subjects so release intent and changelog sections remain clear:

```sh
git add .
git commit -m "feat(gui): add a new batch operation"
git push origin main
```

After tests and all four platform-family builds pass, the workflow updates `pyproject.toml`, `uv.lock`, and `CHANGELOG.md`, pushes a release commit and `vMAJOR.MINOR.PATCH` tag, and publishes the setup files. A failed test or failed platform build creates no tag or release.

The workflow can also be started manually from the Actions tab on `main`, with `auto`, `patch`, `minor`, or `major` selected explicitly. Repository **Workflow permissions** must allow GitHub Actions to write repository contents. If `main` is protected, allow the GitHub Actions bot to push the generated release commit and tag, or adapt the final job to use a release pull request.

For production distribution, configure Windows code signing and Apple signing/notarization in your release process. The repository does not contain private signing material.

## Project layout

```text
assets/                              Desktop icons and integration assets
config/                              Tracked examples; local job files are ignored
src/youtube_audio_video_downloader/  Application package
  cli/                               CLI entry points
  config/                            Settings and runtime-tool discovery
  core/                              Shared filesystem and cancellation utilities
  domain/                            Data models
  gui/                               PyQt6 desktop application
  loaders/                           Job-file loading
  metadata/                          Media tagging
  services/                          Download, editing, search, and library services
  utils/                             Parsing and normalization helpers
tests/                               Automated test suite
tools/desktop_installer.py           Cross-platform graphical installer
tools/release.py                     Validation and release entry point
```

## Application data

Installed builds use a writable per-user data directory:

- Windows: `%APPDATA%\DhimanTools\YouTube Media Studio`
- macOS: `~/Library/Application Support/DhimanTools/YouTube Media Studio`
- Linux: `$XDG_DATA_HOME/DhimanTools/YouTube Media Studio` or `~/.local/share/...`

Use `--data-dir FOLDER` for a one-run CLI override. The GUI can persist a different storage location.

## Troubleshooting

Run this first:

```sh
youtube-media-studio doctor
```

It should report paths for FFmpeg, FFprobe, and Deno plus the installed yt-dlp version.

- Reopen the terminal after enabling the Windows CLI so the updated user `PATH` is loaded.
- Add `~/.local/bin` to `PATH` on macOS/Linux if the shell cannot find the CLI.
- Keep job JSON valid UTF-8 and begin from the sample files.
- Update yt-dlp frequently when running from source because video sites change.
- Include the generated crash report and exact command when reporting a reproducible failure.

## Third-party software

Packaged releases include third-party runtimes. See `THIRD_PARTY_NOTICES.md` for source and licensing details.
