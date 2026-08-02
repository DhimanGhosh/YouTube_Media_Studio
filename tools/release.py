#!/usr/bin/env python3
"""One entry point for validation and platform-native release builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ASSETS = ROOT / "assets"


@dataclass(frozen=True)
class Target:
    name: str
    status: str
    runner: str
    artifact: str
    note: str


TARGETS = {
    "wheel": Target("wheel", "ready", "any", "wheel + source archive", "CLI/core package"),
    "windows": Target("windows", "ready", "windows", "GUI setup EXE", "Per-user GUI installer with optional CLI checkbox"),
    "linux": Target("linux", "ready", "linux", "GUI installer .run", "Per-user GUI installer with optional CLI checkbox"),
    "macos": Target("macos", "ready", "macos", "installer DMG", "Per-user GUI installer with optional CLI checkbox; unsigned"),
    "raspi": Target("raspi", "ready", "any", "CLI installer tar.gz", "Installs on Pi OS arm64; no Qt dependency"),
}


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def host_target() -> str:
    return {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(
        platform.system(), "unsupported"
    )


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def plan(*, json_output: bool = False) -> None:
    payload = {
        "host": host_target(),
        "version": project_version(),
        "targets": [asdict(target) for target in TARGETS.values()],
    }
    if json_output:
        print(json.dumps(payload, indent=2))
        return
    print(f"YouTube Media Studio {payload['version']} (host: {payload['host']})")
    for target in TARGETS.values():
        print(f"  {target.name:8} {target.status:7} {target.runner:11} {target.artifact}")
        if target.status != "ready":
            print(f"           {target.note}")


def list_build_targets() -> None:
    """Print copyable build commands and the host required for every target."""

    print("Available values for build --target:")
    print(
        f"  {'current':8} {'ready':7} {'this host':11} "
        f"native {host_target()} desktop package"
    )
    print("           uv run python tools/release.py build --target current")
    for target in TARGETS.values():
        print(
            f"  {target.name:8} {target.status:7} {target.runner:11} "
            f"{target.artifact}"
        )
        print(
            "           uv run python tools/release.py build "
            f"--target {target.name}"
        )
        if target.note:
            print(f"           {target.note}")


def check() -> None:
    run(["uv", "run", "--group", "dev", "ruff", "check", "src", "tests", "tools"])
    # Qt Multimedia on Windows can terminate natively after hundreds of prior
    # tests have created and released unrelated Qt objects. Keep its focused
    # lifecycle suite in a fresh process while still running every test.
    run(
        [
            "uv",
            "run",
            "--group",
            "dev",
            "pytest",
            "-q",
            "--ignore=tests/test_media_player.py",
        ]
    )
    run(["uv", "run", "--group", "dev", "pytest", "-q", "tests/test_media_player.py"])


def _generated_paths(*, include_ide: bool = False) -> list[Path]:
    """Return only known generated paths, deliberately excluding user/runtime data."""
    candidates = [
        ROOT / "build",
        ROOT / "dist",
        ROOT / ".pytest_cache",
        ROOT / ".ruff_cache",
        ROOT / "__pycache__",
    ]
    if include_ide:
        candidates.append(ROOT / ".idea")
    for base in (ROOT / "src", ROOT / "tests", ROOT / "tools"):
        if not base.exists():
            continue
        candidates.extend(base.rglob("__pycache__"))
        candidates.extend(base.rglob("*.egg-info"))
    candidates.extend(ROOT.glob("*.spec"))
    return sorted(set(candidates), key=lambda path: (len(path.parts), str(path)), reverse=True)


def clean_generated(*, dry_run: bool = False, include_ide: bool = False) -> int:
    """Remove build/cache output using a repository-contained allowlist."""
    _preserve_legacy_windows_release_data(dry_run=dry_run)
    root = ROOT.resolve()
    removed = 0
    for path in _generated_paths(include_ide=include_ide):
        if not path.exists() and not path.is_symlink():
            continue
        resolved = path.resolve()
        if resolved == root or root not in resolved.parents:
            raise RuntimeError(f"Refusing to clean path outside the project: {path}")
        relative = path.relative_to(ROOT)
        print(f"{'Would remove' if dry_run else 'Removing'} {relative}")
        if dry_run:
            removed += 1
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed += 1
    print(f"{'Found' if dry_run else 'Removed'} {removed} generated path(s).")
    print("Preserved source, config/input files, .venv, crash reports, and runtime media.")
    return removed


def _preserve_legacy_windows_release_data(*, dry_run: bool) -> None:
    """Move old beside-the-EXE settings out of ``dist`` before cleaning it."""

    if platform.system() != "Windows":
        return
    source = ROOT / "dist" / "YouTubeMediaStudioData"
    if not source.is_dir():
        return
    appdata = Path(
        os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
    )
    destination = appdata / "DhimanTools" / "YouTube Media Studio"
    print(
        f"{'Would preserve' if dry_run else 'Preserving'} legacy application data "
        f"from {source.relative_to(ROOT)} to {destination}"
    )
    if dry_run:
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        if ".legacy-data-imported" in relative.parts:
            continue
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and target.stat().st_mtime_ns >= item.stat().st_mtime_ns:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def clean_build_outputs() -> None:
    for path in (DIST / "desktop", DIST / "payload", DIST / "installer-output"):
        if path.exists():
            shutil.rmtree(path)
    DIST.mkdir(parents=True, exist_ok=True)


def build_python() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    run(["uv", "run", "--group", "package", "python", "-m", "build", "--outdir", str(DIST)])


def newest_wheel() -> Path:
    wheels = sorted(DIST.glob("*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        build_python()
        wheels = sorted(DIST.glob("*.whl"), key=lambda item: item.stat().st_mtime)
    return wheels[-1]


def build_raspi() -> Path:
    wheel = newest_wheel()
    version = project_version()
    archive = DIST / f"youtube-media-tools-{version}-raspi-cli.tar.gz"
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary) / f"youtube-media-tools-{version}-raspi-cli"
        staging.mkdir()
        shutil.copy2(wheel, staging / wheel.name)
        shutil.copy2(ROOT / "README.md", staging / "README.md")
        installer = staging / "install.sh"
        installer.write_text(
            "#!/bin/sh\nset -eu\n"
            "command -v python3 >/dev/null || { echo 'Python 3.11+ is required'; exit 1; }\n"
            "python3 -m venv \"${HOME}/.local/share/youtube-media-tools\"\n"
            ". \"${HOME}/.local/share/youtube-media-tools/bin/activate\"\n"
            f"python -m pip install --upgrade pip ./{wheel.name}\n"
            "mkdir -p \"${HOME}/.local/bin\"\n"
            "cp ./uninstall.sh \"${HOME}/.local/bin/youtube-media-studio-uninstall\"\n"
            "chmod 755 \"${HOME}/.local/bin/youtube-media-studio-uninstall\"\n"
            "echo 'Installed. Add ~/.local/share/youtube-media-tools/bin and ~/.local/bin to PATH.'\n",
            encoding="utf-8",
            newline="\n",
        )
        installer.chmod(0o755)
        uninstaller = staging / "uninstall.sh"
        uninstaller.write_text(
            "#!/bin/sh\nset -eu\n"
            "install_root=\"${HOME}/.local/share/youtube-media-tools\"\n"
            "case \"${install_root}\" in \"${HOME}/.local/share/youtube-media-tools\") ;; "
            "*) echo 'Refusing unsafe uninstall path' >&2; exit 1 ;; esac\n"
            "rm -rf -- \"${install_root}\"\n"
            "rm -f -- \"${HOME}/.local/bin/youtube-media-studio-uninstall\"\n"
            "echo 'YouTube Media Studio CLI was removed.'\n",
            encoding="utf-8",
            newline="\n",
        )
        uninstaller.chmod(0o755)
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(staging, arcname=staging.name)
    print(f"Created {archive}")
    return archive


def desktop_icon_for_target(target: str) -> Path:
    """Return the native icon source used by a desktop release target."""

    names = {
        "windows": "youtube_media_studio.ico",
        "macos": "youtube_media_studio.icns",
        "linux": "youtube_media_studio_512.png",
    }
    try:
        icon = ASSETS / names[target]
    except KeyError as exc:
        raise ValueError(f"No desktop icon is defined for {target}") from exc
    if not icon.is_file():
        raise FileNotFoundError(f"Missing desktop icon: {icon}")
    return icon


def build_desktop(target: str) -> Path:
    if target != host_target():
        raise SystemExit(
            f"{target} is a native build. Run it on a {target} runner or use 'all' to dispatch CI."
        )
    clean_build_outputs()
    build_root = ROOT / "build"
    work_path = build_root / f"pyinstaller-{target}-{os.getpid()}-{time.time_ns()}"
    spec_path = build_root / "specs"
    work_path.mkdir(parents=True, exist_ok=True)
    spec_path.mkdir(parents=True, exist_ok=True)
    runtime_tools = prepare_runtime_tools(target)
    icon_path = desktop_icon_for_target(target)
    payload = DIST / "payload"
    gui_output = payload / "gui"
    cli_output = payload / "cli"
    uninstaller_output = payload / "uninstaller"
    installer_output = DIST / "installer-output"

    def frozen_command(*, name: str, entry: str, output: Path, windowed: bool, onefile: bool) -> list[str]:
        command = [
            "uv", "run", "--extra", "gui", "--group", "package", "pyinstaller",
            "--noconfirm", "--clean", "--windowed" if windowed else "--console",
            "--onefile" if onefile else "--onedir", "--name", name, entry,
            "--distpath", str(output), "--workpath", str(work_path / name),
            "--specpath", str(spec_path),
        ]
        if target in {"windows", "macos"}:
            command.extend(["--icon", str(icon_path)])
        for binary in runtime_tools:
            command.extend(["--add-binary", f"{binary}{os.pathsep}runtime-tools"])
        command.extend([
            "--collect-all", "yt_dlp_ejs",
            "--add-data", f"{ROOT / 'THIRD_PARTY_NOTICES.md'}{os.pathsep}.",
            "--add-data", f"{ASSETS}{os.pathsep}assets",
        ])
        return command

    run(frozen_command(
        name="YouTubeMediaStudio", entry="run_gui.py", output=gui_output,
        windowed=True, onefile=target == "windows",
    ))
    run(frozen_command(
        name="youtube-media-studio", entry="run_app.py", output=cli_output,
        windowed=False, onefile=True,
    ))

    (payload / "version.txt").write_text(project_version() + "\n", encoding="utf-8")
    uninstaller_name = (
        "Uninstall YouTube Media Studio"
        if target in {"windows", "macos"}
        else "youtube-media-studio-uninstaller"
    )
    uninstaller_command = [
        "uv", "run", "--extra", "gui", "--group", "package", "pyinstaller",
        "--noconfirm", "--clean", "--windowed",
        "--onefile" if target != "macos" else "--onedir",
        "--name", uninstaller_name, "tools/desktop_installer.py",
        "--distpath", str(uninstaller_output),
        "--workpath", str(work_path / "uninstaller"),
        "--specpath", str(spec_path),
    ]
    if target in {"windows", "macos"}:
        uninstaller_command.extend(["--icon", str(icon_path)])
    run(uninstaller_command)

    cli_executable = cli_output / (
        "youtube-media-studio.exe" if target == "windows" else "youtube-media-studio"
    )
    verify_packaged_application(cli_executable)

    if target == "linux":
        app_dir = gui_output / "YouTubeMediaStudio"
        shutil.copy2(
            ASSETS / "youtube_media_studio_512.png",
            app_dir / "youtube-media-studio.png",
        )

    installer_command = [
        "uv", "run", "--extra", "gui", "--group", "package", "pyinstaller",
        "--noconfirm", "--clean", "--windowed", "--onefile" if target != "macos" else "--onedir",
        "--name", "YouTubeMediaStudio-Setup", "tools/desktop_installer.py",
        "--distpath", str(installer_output),
        "--workpath", str(work_path / "installer"),
        "--specpath", str(spec_path),
    ]
    if target == "macos":
        payload_archive = work_path / "payload.tar.gz"
        with tarfile.open(payload_archive, "w:gz") as bundle:
            bundle.add(payload, arcname="payload")
        installer_command.extend(["--add-data", f"{payload_archive}{os.pathsep}."])
    else:
        installer_command.extend(["--add-data", f"{payload}{os.pathsep}payload"])
    if target in {"windows", "macos"}:
        installer_command.extend(["--icon", str(icon_path)])
    run(installer_command)

    version = project_version()
    machine = platform.machine().lower()
    if target == "windows":
        built = installer_output / "YouTubeMediaStudio-Setup.exe"
        artifact = DIST / f"YouTubeMediaStudio-{version}-windows-{machine}-Setup.exe"
        if artifact.exists():
            artifact.unlink()
        built.replace(artifact)
        verify_packaged_installer(artifact)
    elif target == "linux":
        built = installer_output / "YouTubeMediaStudio-Setup"
        artifact = DIST / f"youtube-media-studio-{version}-linux-{machine}-installer.run"
        if artifact.exists():
            artifact.unlink()
        built.replace(artifact)
        artifact.chmod(artifact.stat().st_mode | 0o111)
        verify_packaged_installer(artifact)
    else:
        setup_app = installer_output / "YouTubeMediaStudio-Setup.app"
        artifact = DIST / f"youtube-media-studio-{version}-macos-{machine}-installer.dmg"
        if artifact.exists():
            artifact.unlink()
        run([
            "hdiutil", "create", "-volname", "YouTube Media Studio",
            "-srcfolder", str(setup_app), "-ov", "-format", "UDZO", str(artifact),
        ])
        verify_packaged_installer(
            setup_app / "Contents" / "MacOS" / "YouTubeMediaStudio-Setup"
        )
    shutil.rmtree(payload)
    shutil.rmtree(installer_output)
    print(f"Created {artifact}")
    return artifact


def prepare_runtime_tools(target: str) -> list[Path]:
    """Fetch pinned FFmpeg/FFprobe and the installed Deno binary for packaging."""

    staging = ROOT / "build" / "runtime-tools" / target
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    script = (
        "import json\n"
        "import deno\n"
        "from portable_ffmpeg import FFmpegVersions, get_ffmpeg\n"
        "ffmpeg, ffprobe = get_ffmpeg(FFmpegVersions.V7)\n"
        "print('RUNTIME_TOOLS_JSON=' + json.dumps({"
        "'ffmpeg': str(ffmpeg), 'ffprobe': str(ffprobe), "
        "'deno': str(deno.find_deno_bin())}))\n"
    )
    command = [
        "uv",
        "run",
        "--extra",
        "gui",
        "--group",
        "package",
        "python",
        "-c",
        script,
    ]
    print("+", " ".join(command[:-1]), "<runtime discovery>", flush=True)
    runtime_environment = os.environ.copy()
    runtime_environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=runtime_environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Could not fetch packaging runtime tools:\n"
            + (completed.stderr or completed.stdout)
        )
    marker = next(
        (
            line.removeprefix("RUNTIME_TOOLS_JSON=")
            for line in completed.stdout.splitlines()
            if line.startswith("RUNTIME_TOOLS_JSON=")
        ),
        "",
    )
    if not marker:
        raise RuntimeError(f"Could not discover packaging runtime tools:\n{completed.stdout}")
    discovered = json.loads(marker)
    expected_names = {
        "ffmpeg": "ffmpeg.exe" if target == "windows" else "ffmpeg",
        "ffprobe": "ffprobe.exe" if target == "windows" else "ffprobe",
        "deno": "deno.exe" if target == "windows" else "deno",
    }
    packaged: list[Path] = []
    for tool, output_name in expected_names.items():
        source = Path(discovered[tool])
        if not source.is_file():
            raise RuntimeError(f"Required packaging tool was not found: {source}")
        destination = staging / output_name
        shutil.copy2(source, destination)
        destination.chmod(destination.stat().st_mode | 0o111)
        packaged.append(destination)
    _verify_runtime_tools(packaged)
    return packaged


def _verify_runtime_tools(tools: list[Path]) -> None:
    for binary in tools:
        version_flag = "--version" if binary.stem == "deno" else "-version"
        completed = subprocess.run(
            [str(binary), version_flag],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Bundled runtime check failed for {binary.name}: "
                f"{completed.stderr or completed.stdout}"
            )


def verify_packaged_application(executable: Path) -> None:
    """Run the frozen artifact's doctor command with external tools removed from PATH."""

    if not executable.is_file():
        raise RuntimeError(f"Packaged executable was not found: {executable}")
    environment = os.environ.copy()
    if os.name == "nt":
        system_root = environment.get("SystemRoot", r"C:\Windows")
        environment["PATH"] = os.pathsep.join(
            [str(Path(system_root) / "System32"), system_root]
        )
    else:
        environment["PATH"] = "/usr/bin:/bin"
    completed = subprocess.run(
        [str(executable), "doctor"],
        check=False,
        env=environment,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "The packaged application could not find its bundled FFmpeg, FFprobe, and Deno."
        )


def verify_packaged_installer(installer: Path) -> None:
    """Verify that a frozen installer can extract and locate both payloads."""

    completed = subprocess.run(
        [str(installer), "--check"],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "The packaged installer failed its embedded-payload check:\n"
            + (completed.stderr or completed.stdout)
        )


def write_manifest() -> None:
    generated = {"build-manifest.json", "SHA256SUMS.txt"}
    artifacts = [path for path in DIST.iterdir() if path.is_file() and path.name not in generated]
    manifest = {
        "project": "youtube-media-studio",
        "version": project_version(),
        "host": {"os": platform.system(), "architecture": platform.machine()},
        "artifacts": [],
    }
    for path in sorted(artifacts):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["artifacts"].append({"file": path.name, "sha256": digest, "bytes": path.stat().st_size})
    (DIST / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    checksum_files = sorted(
        path for path in DIST.iterdir() if path.is_file() and path.name != "SHA256SUMS.txt"
    )
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in checksum_files]
    (DIST / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def dispatch(ref: str) -> None:
    if not (ROOT / ".git").exists():
        raise SystemExit("CI dispatch requires a Git checkout. This folder has no .git directory.")
    if shutil.which("gh") is None:
        raise SystemExit("GitHub CLI (gh) is required to dispatch platform-native builders.")
    run(["gh", "workflow", "run", "cross-platform-build.yml", "--ref", ref])
    print("Build dispatched. Use 'gh run watch' and 'gh run download --dir dist/releases'.")


def build_target(target: str) -> None:
    if target == "wheel":
        build_python()
    elif target == "raspi":
        build_raspi()
    elif target == "current":
        current = host_target()
        if current == "unsupported":
            raise SystemExit("This host cannot produce a desktop package.")
        build_desktop(current)
    else:
        build_desktop(target)
    write_manifest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="show the support/build matrix")
    plan_parser.add_argument("--json", action="store_true")
    subparsers.add_parser("check", help="run lint and tests")
    clean_parser = subparsers.add_parser("clean", help="remove generated build and cache files")
    clean_parser.add_argument("--dry-run", action="store_true", help="show without deleting")
    clean_parser.add_argument(
        "--include-ide", action="store_true", help="also remove the optional .idea folder"
    )
    build_parser = subparsers.add_parser("build", help="build one target")
    build_selection = build_parser.add_mutually_exclusive_group(required=True)
    build_selection.add_argument(
        "--target", choices=["current", *TARGETS], help="platform/package to build"
    )
    build_selection.add_argument(
        "--list-targets",
        action="store_true",
        help="show every target, required host, artifact, and copyable command",
    )
    all_parser = subparsers.add_parser("all", help="validate and dispatch every native build")
    all_parser.add_argument("--ref", default="HEAD", help="pushed Git ref for CI")
    all_parser.add_argument("--local-only", action="store_true", help="build host-independent and current-host artifacts only")
    return parser.parse_args()


def main() -> int:
    os.chdir(ROOT)
    args = parse_args()
    if args.command == "plan":
        plan(json_output=args.json)
    elif args.command == "check":
        check()
    elif args.command == "clean":
        clean_generated(dry_run=args.dry_run, include_ide=args.include_ide)
    elif args.command == "build":
        if args.list_targets:
            list_build_targets()
        else:
            build_target(args.target)
    else:
        plan()
        check()
        build_python()
        build_raspi()
        build_desktop(host_target())
        write_manifest()
        if not args.local_only:
            dispatch(args.ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
