#!/usr/bin/env python3
"""Per-user graphical installer for frozen desktop release payloads."""

from __future__ import annotations

import atexit
import base64
import os
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from youtube_audio_video_downloader.config.app_identity import (
    APP_DISPLAY_NAME,
    CLI_COMMAND,
    DESKTOP_FILE_ID,
    EXECUTABLE_BASENAME,
    ORGANIZATION_NAME,
    WINDOWS_UNINSTALL_KEY,
)

APP_NAME = APP_DISPLAY_NAME
CLI_NAME = CLI_COMMAND
UNINSTALL_KEY = WINDOWS_UNINSTALL_KEY
_EXTRACTED_PAYLOAD_ROOT: Path | None = None


def payload_root() -> Path:
    global _EXTRACTED_PAYLOAD_ROOT

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        bundled_root = Path(frozen_root)
        direct_payload = bundled_root / "payload"
        if direct_payload.is_dir():
            return direct_payload
        archive = bundled_root / "payload.tar.gz"
        if archive.is_file():
            if _EXTRACTED_PAYLOAD_ROOT is None:
                extraction_root = Path(tempfile.mkdtemp(prefix=f"{DESKTOP_FILE_ID}-setup-"))
                try:
                    with tarfile.open(archive, "r:gz") as bundle:
                        bundle.extractall(extraction_root, filter="data")
                except Exception:
                    shutil.rmtree(extraction_root, ignore_errors=True)
                    raise
                _EXTRACTED_PAYLOAD_ROOT = extraction_root / "payload"
                atexit.register(shutil.rmtree, extraction_root, ignore_errors=True)
            return _EXTRACTED_PAYLOAD_ROOT
    return Path(__file__).resolve().parents[1] / "dist" / "payload"


def default_gui_destination() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Programs" / APP_NAME
    if system == "Darwin":
        return Path.home() / "Applications" / f"{EXECUTABLE_BASENAME}.app"
    return Path.home() / ".local" / "opt" / DESKTOP_FILE_ID


def cli_destination(gui_destination: Path | None = None) -> Path:
    if platform.system() == "Windows":
        return (gui_destination or default_gui_destination()) / f"{CLI_NAME}.exe"
    return Path.home() / ".local" / "bin" / CLI_NAME


def gui_payload() -> Path:
    system = platform.system()
    name = {
        "Windows": f"{EXECUTABLE_BASENAME}.exe",
        "Darwin": f"{EXECUTABLE_BASENAME}.app",
        "Linux": EXECUTABLE_BASENAME,
    }.get(system)
    if name is None:
        raise RuntimeError(f"Unsupported operating system: {system}")
    return payload_root() / "gui" / name


def cli_payload() -> Path:
    suffix = ".exe" if platform.system() == "Windows" else ""
    return payload_root() / "cli" / f"{CLI_NAME}{suffix}"


def uninstaller_payload() -> Path:
    name = {
        "Windows": f"Uninstall {APP_NAME}.exe",
        "Darwin": f"Uninstall {APP_NAME}.app",
        "Linux": f"{CLI_NAME}-uninstaller",
    }.get(platform.system())
    if name is None:
        raise RuntimeError(f"Unsupported operating system: {platform.system()}")
    return payload_root() / "uninstaller" / name


def app_version() -> str:
    version_file = payload_root() / "version.txt"
    return version_file.read_text(encoding="utf-8").strip()


def uninstaller_destination(gui_destination: Path | None = None) -> Path:
    system = platform.system()
    if system == "Windows":
        return (gui_destination or default_gui_destination()) / f"Uninstall {APP_NAME}.exe"
    if system == "Darwin":
        return Path.home() / "Applications" / f"Uninstall {APP_NAME}.app"
    return (gui_destination or default_gui_destination()) / f"{CLI_NAME}-uninstaller"


def application_data_directory() -> Path:
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / ORGANIZATION_NAME / APP_NAME


def existing_installation_destination() -> Path | None:
    """Return the registered/current installation without inventing a new path."""
    if platform.system() == "Windows":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                location, _ = winreg.QueryValueEx(key, "InstallLocation")
        except (FileNotFoundError, OSError):
            location = ""
        if str(location).strip():
            registered = Path(str(location)).expanduser()
            if registered.exists():
                return registered

    default = default_gui_destination()
    return default if default.exists() else None


def existing_installation_version() -> str | None:
    if platform.system() != "Windows":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            version, _ = winreg.QueryValueEx(key, "DisplayVersion")
    except (FileNotFoundError, OSError):
        return None
    return str(version).strip() or None


def _installed_gui_executable(gui_target: Path) -> Path:
    system = platform.system()
    if system == "Windows":
        return gui_target / f"{EXECUTABLE_BASENAME}.exe"
    if system == "Darwin":
        return gui_target / "Contents" / "MacOS" / EXECUTABLE_BASENAME
    return gui_target / EXECUTABLE_BASENAME


def _stop_windows_application(executable: Path) -> tuple[int, ...]:
    """Stop only processes whose full image path is the installed GUI executable."""
    script = (
        "$target = [IO.Path]::GetFullPath($env:YMS_TARGET_PATH); "
        "$matches = Get-CimInstance Win32_Process | Where-Object { "
        "$_.Name -eq $env:YMS_TARGET_NAME -and $_.ExecutablePath -and [String]::Equals("
        "[IO.Path]::GetFullPath($_.ExecutablePath), $target, "
        "[StringComparison]::OrdinalIgnoreCase) }; "
        "foreach ($process in $matches) { "
        "$native = Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue; "
        "if ($native -and $native.MainWindowHandle -ne 0) { "
        "$null = $native.CloseMainWindow(); "
        "$deadline = (Get-Date).AddSeconds(5); "
        "while ((Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) "
        "-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 } }; "
        "if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) { "
        "Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop; "
        "$deadline = (Get-Date).AddSeconds(10); "
        "while ((Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) "
        "-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 } }; "
        "Write-Output $process.ProcessId }"
    )
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    environment = os.environ.copy()
    environment["YMS_TARGET_PATH"] = str(executable.resolve())
    environment["YMS_TARGET_NAME"] = executable.name
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"Could not close the running {APP_NAME} application. "
            f"Close it manually and try again. {detail}"
        )
    return tuple(int(line) for line in completed.stdout.splitlines() if line.strip().isdigit())


def _posix_process_executable(pid: int) -> Path | None:
    if platform.system() == "Linux":
        try:
            return (Path("/proc") / str(pid) / "exe").resolve(strict=True)
        except (FileNotFoundError, PermissionError, OSError):
            return None
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "comm="],
        check=False,
        capture_output=True,
        text=True,
    )
    command = completed.stdout.strip()
    return Path(command).resolve() if completed.returncode == 0 and command else None


def _stop_posix_application(executable: Path) -> tuple[int, ...]:
    target = executable.resolve()
    completed = subprocess.run(["ps", "-axo", "pid="], check=True, capture_output=True, text=True)
    matches = tuple(
        pid
        for value in completed.stdout.splitlines()
        if value.strip().isdigit()
        for pid in (int(value.strip()),)
        if pid != os.getpid() and _posix_process_executable(pid) == target
    )
    for pid in matches:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    remaining = set(matches)
    while remaining and time.monotonic() < deadline:
        for pid in tuple(remaining):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                remaining.remove(pid)
        if remaining:
            time.sleep(0.1)
    for pid in remaining:
        os.kill(pid, signal.SIGKILL)
    return matches


def stop_running_application(gui_target: Path) -> tuple[int, ...]:
    """Close a running installed GUI at *gui_target*, never a name-only match."""
    executable = _installed_gui_executable(gui_target)
    if not executable.exists():
        return ()
    if platform.system() == "Windows":
        return _stop_windows_application(executable)
    return _stop_posix_application(executable)


def _remove_existing_installation(gui_target: Path) -> bool:
    """Remove an identified application install while preserving per-user data."""
    executable = _installed_gui_executable(gui_target)
    detected = None if executable.exists() else existing_installation_destination()
    is_registered_target = detected is not None and os.path.normcase(
        str(detected.resolve())
    ) == os.path.normcase(str(gui_target.resolve()))
    is_existing_install = executable.exists() or is_registered_target
    stop_running_application(gui_target)
    if not is_existing_install:
        return False

    system = platform.system()
    if system == "Windows":
        _windows_path(False, gui_target)
        _windows_unregister()
        shortcut = (
            Path(os.environ["APPDATA"])
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / f"{APP_NAME}.lnk"
        )
        _remove_path(shortcut)
    elif system == "Linux":
        applications = Path.home() / ".local" / "share" / "applications"
        _remove_path(applications / f"{DESKTOP_FILE_ID}.desktop")
        _remove_path(applications / f"{DESKTOP_FILE_ID}-uninstall.desktop")

    _remove_path(gui_target)
    if system != "Windows":
        _remove_path(cli_destination(gui_target))
        _remove_path(uninstaller_destination(gui_target))
    return True


def check_payload() -> None:
    gui = gui_payload()
    cli = cli_payload()
    uninstaller = uninstaller_payload()
    if not gui.exists():
        raise FileNotFoundError(f"Installer GUI payload is missing: {gui}")
    if not cli.is_file():
        raise FileNotFoundError(f"Installer CLI payload is missing: {cli}")
    if not uninstaller.exists():
        raise FileNotFoundError(f"Installer uninstaller payload is missing: {uninstaller}")
    if not app_version():
        raise RuntimeError("Installer version payload is empty")
    print(f"GUI payload: {gui}")
    print(f"CLI payload: {cli}")
    print(f"Uninstaller payload: {uninstaller}")


def _replace_path(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    elif destination.exists() or destination.is_symlink():
        destination.unlink()
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)
        destination.chmod(destination.stat().st_mode | 0o111)


def _windows_path(enable: bool, install_root: Path | None = None) -> None:
    import winreg

    install_dir = str(install_root or default_gui_destination())
    key_path = r"Environment"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        try:
            current, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current = ""
        entries = [part for part in str(current).split(os.pathsep) if part]
        entries = [
            part for part in entries if os.path.normcase(part) != os.path.normcase(install_dir)
        ]
        if enable:
            entries.append(install_dir)
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, os.pathsep.join(entries))


def _windows_shortcut(executable: Path) -> None:
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    start_menu.mkdir(parents=True, exist_ok=True)
    shortcut = start_menu / f"{APP_NAME}.lnk"
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut($env:YMS_SHORTCUT_PATH); "
        "$link.TargetPath = $env:YMS_TARGET_PATH; "
        "$link.WorkingDirectory = $env:YMS_WORKING_DIRECTORY; "
        "$link.IconLocation = $env:YMS_TARGET_PATH + ',0'; "
        "$link.Save()"
    )
    encoded_script = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    environment = os.environ.copy()
    environment.update(
        {
            "YMS_SHORTCUT_PATH": str(shortcut),
            "YMS_TARGET_PATH": str(executable),
            "YMS_WORKING_DIRECTORY": str(executable.parent),
        }
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded_script,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Could not create the Start-menu shortcut: {detail}")


def _windows_unregister() -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass


def _windows_register_uninstaller(uninstaller: Path, executable: Path) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        values = {
            "DisplayName": APP_NAME,
            "DisplayVersion": app_version(),
            "DisplayIcon": str(executable),
            "Publisher": ORGANIZATION_NAME,
            "InstallLocation": str(executable.parent),
            "UninstallString": f'"{uninstaller}" --uninstall',
            "QuietUninstallString": f'"{uninstaller}" --uninstall --yes',
        }
        for name, value in values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def _linux_desktop_entry(executable: Path) -> None:
    applications = Path.home() / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    icon = executable.parent / f"{DESKTOP_FILE_ID}.png"
    entry = applications / f"{DESKTOP_FILE_ID}.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        f"Exec={executable}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=AudioVideo;Utility;\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)


def _linux_uninstaller_entry(uninstaller: Path) -> None:
    applications = Path.home() / ".local" / "share" / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    entry = applications / f"{DESKTOP_FILE_ID}-uninstall.desktop"
    entry.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name=Uninstall {APP_NAME}\n"
        f"Exec={uninstaller} --uninstall\n"
        "Terminal=false\n"
        "Categories=Settings;Utility;\n",
        encoding="utf-8",
    )
    entry.chmod(0o755)


def install(include_cli: bool, gui_destination: Path | None = None) -> tuple[Path, Path | None]:
    source_gui = gui_payload()
    source_cli = cli_payload()
    source_uninstaller = uninstaller_payload()
    if not source_gui.exists():
        raise FileNotFoundError(f"Installer GUI payload is missing: {source_gui}")
    if include_cli and not source_cli.is_file():
        raise FileNotFoundError(f"Installer CLI payload is missing: {source_cli}")
    if not source_uninstaller.exists():
        raise FileNotFoundError(f"Installer uninstaller payload is missing: {source_uninstaller}")

    custom_destination = gui_destination is not None
    gui_target = gui_destination or default_gui_destination()
    system = platform.system()
    _remove_existing_installation(gui_target)
    if system == "Windows":
        gui_executable = gui_target / f"{EXECUTABLE_BASENAME}.exe"
        try:
            _replace_path(source_gui, gui_executable)
        except PermissionError:
            # Cover the narrow race where the application starts after preflight.
            stop_running_application(gui_target)
            _replace_path(source_gui, gui_executable)
    elif system == "Darwin":
        _replace_path(source_gui, gui_target)
        gui_executable = gui_target / "Contents" / "MacOS" / EXECUTABLE_BASENAME
    else:
        _replace_path(source_gui, gui_target)
        gui_executable = gui_target / EXECUTABLE_BASENAME
        _linux_desktop_entry(gui_executable)

    installed_uninstaller = (
        uninstaller_destination(gui_target) if custom_destination else uninstaller_destination()
    )
    _replace_path(source_uninstaller, installed_uninstaller)
    if system == "Windows":
        _windows_register_uninstaller(installed_uninstaller, gui_executable)
        _windows_shortcut(gui_executable)
    elif system == "Linux":
        _linux_uninstaller_entry(installed_uninstaller)

    installed_cli = None
    if include_cli:
        installed_cli = cli_destination(gui_target) if custom_destination else cli_destination()
        _replace_path(source_cli, installed_cli)
        if system == "Windows":
            if custom_destination:
                _windows_path(True, gui_target)
            else:
                _windows_path(True)
    return gui_target, installed_cli


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _remove_windows_installation(install_root: Path | None = None) -> None:
    import ctypes

    install_root = install_root or default_gui_destination()
    running = Path(sys.executable).resolve()
    if install_root.is_dir():
        for child in install_root.iterdir():
            if child.resolve() == running:
                continue
            _remove_path(child)
    movefile_delay_until_reboot = 0x4
    ctypes.windll.kernel32.MoveFileExW(str(running), None, movefile_delay_until_reboot)
    ctypes.windll.kernel32.MoveFileExW(str(install_root), None, movefile_delay_until_reboot)


def uninstall(*, remove_data: bool = False) -> None:
    system = platform.system()
    if system == "Windows":
        running_uninstaller = "uninstall" in Path(sys.executable).name.lower()
        install_root = (
            Path(sys.executable).resolve().parent
            if running_uninstaller
            else default_gui_destination()
        )
        _windows_path(False, install_root)
        _windows_unregister()
        shortcut = (
            Path(os.environ["APPDATA"])
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / f"{APP_NAME}.lnk"
        )
        _remove_path(shortcut)
        _remove_windows_installation(install_root)
    else:
        _remove_path(cli_destination())
        if system == "Linux":
            applications = Path.home() / ".local" / "share" / "applications"
            _remove_path(applications / f"{DESKTOP_FILE_ID}.desktop")
            _remove_path(applications / f"{DESKTOP_FILE_ID}-uninstall.desktop")
        _remove_path(default_gui_destination())
        _remove_path(uninstaller_destination())
    if remove_data:
        _remove_path(application_data_directory())


INSTALLER_STYLE = """
QWidget#installerWindow, QWidget#pageContent { background: #ffffff; }
QLabel#eyebrow { color: #555555; font-size: 11px; font-weight: 600; }
QLabel#pageTitle { color: #111111; font-size: 22px; font-weight: 600; }
QLabel#bodyText { color: #282828; font-size: 13px; }
QLabel#detailText { color: #666666; font-size: 12px; }
QLabel#successMark {
    background: #e8f4e8; color: #167438; border: 1px solid #9ac7a7;
    border-radius: 30px; font-size: 16px; font-weight: 700;
}
QFrame#footer { background: #f3f3f3; border-top: 1px solid #c9c9c9; }
QGroupBox { font-weight: 600; }
QPushButton {
    min-height: 26px; padding: 0 12px; color: #111111;
    background: #f5f5f5; border: 1px solid #a9a9a9; border-radius: 2px;
}
QPushButton:hover { background: #e9f3fb; border-color: #0078d4; }
QPushButton:pressed { background: #dcebf7; }
QPushButton:default { border: 2px solid #0078d4; }
QPushButton:disabled { color: #777777; background: #eeeeee; border-color: #c8c8c8; }
QProgressBar {
    height: 18px; text-align: center;
}
"""


def apply_installer_palette(app: QApplication) -> None:
    """Keep native controls readable when the operating system uses a dark theme."""
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f3f3f3"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0067c0"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#666666"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#777777"))
    app.setPalette(palette)


class InstallerArtwork(QWidget):
    """Scalable, dependency-free artwork for the setup wizard side panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(245)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor("#172033"))
        gradient.setColorAt(1, QColor("#263a63"))
        painter.fillRect(self.rect(), gradient)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(229, 85, 63, 34))
        painter.drawEllipse(QPointF(38, 70), 112, 112)
        painter.setBrush(QColor(255, 255, 255, 18))
        painter.drawEllipse(QPointF(205, 330), 155, 155)

        painter.setPen(QColor("#ffffff"))
        brand = QFont("Segoe UI")
        brand.setPointSize(18)
        brand.setWeight(QFont.Weight.Bold)
        painter.setFont(brand)
        painter.drawText(
            QRectF(28, 34, 190, 60), Qt.AlignmentFlag.AlignLeft, "YouTube\nMedia Studio"
        )

        # A media-window motif keeps the panel recognizable without shipping artwork.
        card = QRectF(29, 155, 187, 132)
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(card, 10, 10)
        painter.setBrush(QColor("#e9edf4"))
        painter.drawRoundedRect(QRectF(43, 174, 159, 88), 5, 5)
        painter.setBrush(QColor("#e5553f"))
        play = QPainterPath()
        play.moveTo(111, 196)
        play.lineTo(111, 241)
        play.lineTo(149, 218.5)
        play.closeSubpath()
        painter.drawPath(play)
        painter.setPen(QPen(QColor("#c5ccd8"), 3))
        painter.drawLine(53, 273, 127, 273)

        painter.setPen(QColor("#cbd5e6"))
        small = QFont("Segoe UI")
        small.setPointSize(9)
        painter.setFont(small)
        painter.drawText(QRectF(28, self.height() - 68, 190, 22), "SETUP  /  SAFE  /  PER-USER")
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(28, self.height() - 45, 190, 22), "Everything you need is included")


class InstallWorker(QThread):
    succeeded = pyqtSignal(object, object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        include_cli: bool,
        destination: Path,
        operation: str = "install",
        remove_data: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.include_cli = include_cli
        self.destination = destination
        self.operation = operation
        self.remove_data = remove_data

    def run(self) -> None:
        try:
            if self.operation == "uninstall":
                if not _remove_existing_installation(self.destination):
                    raise RuntimeError(f"No {APP_NAME} installation was found to remove.")
                if self.remove_data:
                    _remove_path(application_data_directory())
                gui_target, installed_cli = None, None
            else:
                gui_target, installed_cli = install(self.include_cli, self.destination)
        except Exception as exc:  # noqa: BLE001 - errors must reach the installer user
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(gui_target, installed_cli)


def _page_header(step: str, title: str, body: str) -> tuple[QVBoxLayout, QLabel]:
    layout = QVBoxLayout()
    layout.setContentsMargins(40, 36, 40, 28)
    layout.setSpacing(12)
    eyebrow = QLabel(step.upper())
    eyebrow.setObjectName("eyebrow")
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    description = QLabel(body)
    description.setObjectName("bodyText")
    description.setWordWrap(True)
    layout.addWidget(eyebrow)
    layout.addWidget(heading)
    layout.addWidget(description)
    return layout, description


class InstallerWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("installerWindow")
        self.setWindowTitle(f"{APP_NAME} Setup")
        self.setFixedSize(780, 520)
        self._worker: InstallWorker | None = None
        self._installed_gui: Path | None = None
        self._existing_destination = existing_installation_destination()
        self._existing_version = existing_installation_version()

        self.pages = QStackedWidget()
        self.pages.addWidget(self._welcome_page())
        self.pages.addWidget(self._destination_page())
        self.pages.addWidget(self._components_page())
        self.pages.addWidget(self._ready_page())
        self.pages.addWidget(self._progress_page())
        self.pages.addWidget(self._finish_page())

        self.back_button = QPushButton("Back")
        self.back_button.setMinimumWidth(88)
        self.back_button.clicked.connect(self.go_back)
        self.next_button = QPushButton("Next  >")
        self.next_button.setMinimumWidth(88)
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self.go_next)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setMinimumWidth(88)
        self.cancel_button.clicked.connect(self.close)

        footer = QFrame()
        footer.setObjectName("footer")
        footer.setFixedHeight(66)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(24, 12, 24, 12)
        footer_layout.addStretch()
        footer_layout.addWidget(self.back_button)
        footer_layout.addWidget(self.next_button)
        footer_layout.addWidget(self.cancel_button)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(InstallerArtwork())
        content_layout.addWidget(self.pages, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(content, 1)
        layout.addWidget(footer)
        self._sync_buttons()

    def _welcome_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageContent")
        if self._existing_destination:
            installed_label = f" version {self._existing_version}" if self._existing_version else ""
            layout, _ = _page_header(
                "Maintenance",
                f"Maintain {APP_NAME}",
                f"Setup found{installed_label} at {self._existing_destination}. "
                "Choose the operation you want to perform.",
            )
            self.upgrade_option = QRadioButton(f"Upgrade to version {app_version()} (recommended)")
            self.upgrade_option.setChecked(True)
            self.repair_option = QRadioButton("Repair installation (replace all program binaries)")
            self.uninstall_option = QRadioButton("Uninstall YouTube Media Studio")
            self.remove_data_option = QCheckBox(
                "Also remove settings, history, and application data"
            )
            self.remove_data_option.setEnabled(False)
            self.uninstall_option.toggled.connect(self.remove_data_option.setEnabled)

            choices = QGroupBox("Choose a maintenance operation")
            choices_layout = QVBoxLayout(choices)
            choices_layout.setContentsMargins(14, 18, 14, 14)
            choices_layout.setSpacing(6)
            upgrade_detail = QLabel("Replace the installed release with this newer setup package.")
            upgrade_detail.setObjectName("detailText")
            repair_detail = QLabel(
                "Reinstall every program file to repair missing or damaged binaries."
            )
            repair_detail.setObjectName("detailText")
            uninstall_detail = QLabel(
                "Remove the application, command-line tools, shortcuts, and registration."
            )
            uninstall_detail.setObjectName("detailText")
            choices_layout.addWidget(self.upgrade_option)
            choices_layout.addWidget(upgrade_detail)
            choices_layout.addSpacing(4)
            choices_layout.addWidget(self.repair_option)
            choices_layout.addWidget(repair_detail)
            choices_layout.addSpacing(4)
            choices_layout.addWidget(self.uninstall_option)
            choices_layout.addWidget(uninstall_detail)
            choices_layout.addWidget(self.remove_data_option)
            layout.addSpacing(8)
            layout.addWidget(choices)
            layout.addStretch()
            page.setLayout(layout)
            return page

        layout, _ = _page_header(
            "Welcome",
            f"Install {APP_NAME}",
            "This wizard will install the desktop application for your user account.",
        )
        details = QLabel(
            "Python, FFmpeg, FFprobe, Deno, and all application dependencies are included. "
            "No administrator access or separate runtime setup is required."
        )
        details.setObjectName("bodyText")
        details.setWordWrap(True)
        layout.addSpacing(8)
        layout.addWidget(details)
        layout.addStretch()
        hint = QLabel("Select Next to review your installation options.")
        hint.setObjectName("detailText")
        layout.addWidget(hint)
        page.setLayout(layout)
        return page

    def _destination_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageContent")
        layout, _ = _page_header(
            "Destination Folder",
            "Choose where to install",
            f"Setup will install {APP_NAME} in the following folder.",
        )

        card = QGroupBox("Install path")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 18, 14, 14)
        path_row = QHBoxLayout()
        detected_destination = self._existing_destination
        self.destination_edit = QLineEdit(str(detected_destination or default_gui_destination()))
        self.destination_edit.setMinimumHeight(26)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self.browse_destination)
        path_row.addWidget(self.destination_edit, 1)
        path_row.addWidget(browse_button)
        card_layout.addLayout(path_row)
        note_text = (
            "Existing installation detected. Setup will upgrade it and keep your settings."
            if detected_destination
            else "A new folder will be created if it does not already exist."
        )
        note = QLabel(note_text)
        note.setObjectName("detailText")
        card_layout.addWidget(note)
        layout.addWidget(card)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _components_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageContent")
        layout, _ = _page_header(
            "Optional Components",
            "Select additional components",
            "Choose whether to install command-line access in addition to the desktop app.",
        )

        self.cli = QCheckBox("Install command-line tools (recommended)")
        self.cli.setChecked(True)
        self.cli.setMinimumHeight(24)
        cli_detail = QLabel()
        cli_detail.setObjectName("detailText")
        cli_detail.setWordWrap(True)
        if platform.system() == "Windows":
            cli_detail.setText(f"Adds {CLI_NAME} to your user PATH.")
        else:
            cli_detail.setText(f"Installs {CLI_NAME} in ~/.local/bin.")
        layout.addWidget(self.cli)
        layout.addWidget(cli_detail)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _ready_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageContent")
        layout, _ = _page_header(
            "Ready to Install",
            "Ready to install",
            "Review your choices, then select Install to begin.",
        )
        self.ready_title = layout.itemAt(1).widget()
        self.ready_description = layout.itemAt(2).widget()
        self.ready_summary = QLabel()
        self.ready_summary.setObjectName("bodyText")
        self.ready_summary.setWordWrap(True)
        self.ready_summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.ready_summary)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _progress_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageContent")
        layout, _ = _page_header(
            "Installing",
            "Installing...",
            f"Please wait while {APP_NAME} is installed.",
        )
        self.progress_title = layout.itemAt(1).widget()
        self.progress_description = layout.itemAt(2).widget()
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress_status = QLabel("Preparing files and operating-system integration...")
        self.progress_status.setObjectName("detailText")
        self.progress_status.setWordWrap(True)
        layout.addSpacing(14)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_status)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _finish_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("pageContent")
        layout, _ = _page_header(
            "Complete",
            "You're ready to go",
            f"{APP_NAME} was installed successfully.",
        )
        self.finish_title = layout.itemAt(1).widget()
        self.finish_description = layout.itemAt(2).widget()
        mark_row = QHBoxLayout()
        self.success_mark = QLabel("OK")
        self.success_mark.setObjectName("successMark")
        self.success_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.success_mark.setFixedSize(60, 60)
        mark_row.addWidget(self.success_mark)
        mark_row.addStretch()
        self.finish_detail = QLabel("")
        self.finish_detail.setObjectName("bodyText")
        self.finish_detail.setWordWrap(True)
        self.finish_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addSpacing(8)
        layout.addLayout(mark_row)
        layout.addWidget(self.finish_detail)
        layout.addStretch()
        page.setLayout(layout)
        return page

    def _sync_buttons(self) -> None:
        page = self.pages.currentIndex()
        self.back_button.setVisible(page in (1, 2, 3))
        self.next_button.setVisible(page != 4)
        self.cancel_button.setVisible(page != 5)
        if page == 3:
            labels = {"upgrade": "Upgrade", "repair": "Repair", "uninstall": "Uninstall"}
            self.next_button.setText(labels.get(self._selected_operation(), "Install"))
        else:
            self.next_button.setText("Finish" if page == 5 else "Next")

    def go_back(self) -> None:
        page = self.pages.currentIndex()
        if page in (1, 2, 3):
            if page == 3 and self._selected_operation() == "uninstall":
                self.pages.setCurrentIndex(0)
            else:
                self.pages.setCurrentIndex(page - 1)
            self._sync_buttons()

    def go_next(self) -> None:
        page = self.pages.currentIndex()
        if page == 0 and self._selected_operation() == "uninstall":
            self._update_ready_summary()
            self.pages.setCurrentIndex(3)
            self._sync_buttons()
        elif page in (0, 1):
            if page == 1 and not self.destination_edit.text().strip():
                QMessageBox.warning(self, "Choose an install path", "Enter an installation folder.")
                return
            self.pages.setCurrentIndex(page + 1)
            self._sync_buttons()
            if page == 0:
                self.destination_edit.setCursorPosition(0)
                self.next_button.setFocus()
        elif page == 2:
            self._update_ready_summary()
            self.pages.setCurrentIndex(3)
            self._sync_buttons()
        elif page == 3:
            self.run_install()
        elif page == 5:
            self.close()

    def browse_destination(self) -> None:
        current = Path(self.destination_edit.text()).expanduser()
        start = current if current.is_dir() else current.parent
        selected = QFileDialog.getExistingDirectory(self, "Choose Installation Folder", str(start))
        if selected:
            self.destination_edit.setText(selected)

    def _update_ready_summary(self) -> None:
        operation = self._selected_operation()
        labels = {
            "install": ("Ready to install", "Review your choices, then select Install to begin."),
            "upgrade": ("Ready to upgrade", "Review your choices, then select Upgrade to begin."),
            "repair": ("Ready to repair", "Review your choices, then select Repair to begin."),
            "uninstall": (
                "Ready to uninstall",
                "Review your choices, then select Uninstall to remove the application.",
            ),
        }
        title, description = labels[operation]
        self.ready_title.setText(title)
        self.ready_description.setText(description)
        if operation == "uninstall":
            data_note = (
                "Settings, history, and application data will also be removed."
                if self.remove_data_option.isChecked()
                else "Settings, history, and application data will be kept."
            )
            self.ready_summary.setText(
                f"Operation:\nUninstall {APP_NAME}\n\n"
                f"Installation folder:\n{self.destination_edit.text().strip()}\n\n{data_note}"
            )
            return
        components = (
            "Desktop application and command-line tools"
            if self.cli.isChecked()
            else "Desktop application"
        )
        destination = Path(self.destination_edit.text().strip())
        upgrade_note = ""
        if _installed_gui_executable(destination).exists():
            action = "upgraded" if operation == "upgrade" else "repaired"
            upgrade_note = (
                f"\n\n{action.title()}:\nThe existing application will be closed "
                "automatically and replaced with fresh binaries. Settings, history, "
                "and application data will be kept."
            )
        self.ready_summary.setText(
            f"Destination folder:\n{self.destination_edit.text().strip()}\n\n"
            f"Components:\n{components}{upgrade_note}"
        )

    def run_install(self) -> None:
        operation = self._selected_operation()
        progress_labels = {
            "install": ("Installing...", f"Please wait while {APP_NAME} is installed."),
            "upgrade": ("Upgrading...", f"Please wait while {APP_NAME} is upgraded."),
            "repair": ("Repairing...", f"Please wait while {APP_NAME} is repaired."),
            "uninstall": ("Uninstalling...", f"Please wait while {APP_NAME} is removed."),
        }
        progress_title, progress_description = progress_labels[operation]
        self.progress_title.setText(progress_title)
        self.progress_description.setText(progress_description)
        self.pages.setCurrentIndex(4)
        self._sync_buttons()
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(False)
        self._worker = InstallWorker(
            self.cli.isChecked(),
            Path(self.destination_edit.text().strip()),
            operation,
            self.remove_data_option.isChecked() if self._existing_destination else False,
            self,
        )
        self._worker.succeeded.connect(self._installation_complete)
        self._worker.failed.connect(self._installation_failed)
        self._worker.start()

    def _installation_complete(self, gui_target: Path | None, installed_cli: Path | None) -> None:
        if self._selected_operation() == "uninstall":
            self.finish_title.setText("Uninstall complete")
            self.finish_description.setText(f"{APP_NAME} was removed successfully.")
            detail = "Settings, history, and application data were removed."
            if not self.remove_data_option.isChecked():
                detail = f"Your application data was kept at:\n{application_data_directory()}"
            self.finish_detail.setText(detail)
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.pages.setCurrentIndex(5)
            self.cancel_button.setEnabled(True)
            self._sync_buttons()
            return
        if gui_target is None:
            self._installation_failed("Setup did not return an installation folder.")
            return
        self._installed_gui = gui_target
        detail = f"Installed to:\n{gui_target}"
        if installed_cli:
            detail += f"\n\nCommand-line tools:\n{installed_cli}"
            if platform.system() != "Windows":
                detail += "\n\nMake sure ~/.local/bin is in your PATH."
        self.finish_detail.setText(detail)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.pages.setCurrentIndex(5)
        self.cancel_button.setEnabled(True)
        self._sync_buttons()

    def _selected_operation(self) -> str:
        if not self._existing_destination:
            return "install"
        if self.uninstall_option.isChecked():
            return "uninstall"
        if self.repair_option.isChecked():
            return "repair"
        return "upgrade"

    def _installation_failed(self, detail: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.pages.setCurrentIndex(3)
        self.cancel_button.setEnabled(True)
        self._sync_buttons()
        QMessageBox.critical(self, "Installation failed", detail)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._worker is not None and self._worker.isRunning():
            event.ignore()
            return
        super().closeEvent(event)


class UninstallerWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Uninstall {APP_NAME}")
        self.setMinimumWidth(500)

        title = QLabel(f"Uninstall {APP_NAME}")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        summary = QLabel(
            "Remove the desktop application, optional command-line interface, "
            "shortcuts, and operating-system integration."
        )
        summary.setWordWrap(True)
        self.remove_data = QCheckBox("Also remove settings, history, and application data")
        self.remove_data.setChecked(False)

        uninstall_button = QPushButton("Uninstall")
        uninstall_button.clicked.connect(self.run_uninstall)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel_button)
        buttons.addWidget(uninstall_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(summary)
        layout.addWidget(self.remove_data)
        layout.addStretch()
        layout.addLayout(buttons)

    def run_uninstall(self) -> None:
        answer = QMessageBox.question(
            self,
            f"Uninstall {APP_NAME}?",
            "The installed application and command-line tools will be removed.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            uninstall(remove_data=self.remove_data.isChecked())
        except Exception as exc:  # noqa: BLE001 - errors must reach the uninstaller user
            QMessageBox.critical(self, "Uninstallation failed", str(exc))
            return
        QMessageBox.information(self, "Uninstallation complete", f"Removed {APP_NAME}.")
        self.close()


def main() -> int:
    if "--check" in sys.argv[1:]:
        check_payload()
        return 0
    uninstall_mode = (
        "--uninstall" in sys.argv[1:] or "uninstall" in Path(sys.executable).name.lower()
    )
    if uninstall_mode and "--yes" in sys.argv[1:]:
        uninstall(remove_data="--remove-data" in sys.argv[1:])
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    apply_installer_palette(app)
    app.setStyleSheet(INSTALLER_STYLE)
    window = UninstallerWindow() if uninstall_mode else InstallerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
