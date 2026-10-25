#!/usr/bin/env python3
"""Cursober: safely apply the active Linux cursor theme to Sober's cursor overlay."""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil
import struct
import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Cursober"
SOBER_ID = "org.vinegarhq.Sober"
IMAGE_TYPE = 0xFFFD0002
TEXTURE_SIZE = 64
TARGETS = {
    "ArrowCursor.png": ("Normal", ("left_ptr", "default", "arrow", "X_cursor")),
    "ArrowFarCursor.png": ("Interactive", ("hand2", "link", "pointer", "hand1")),
}


class CursoberError(RuntimeError):
    pass


@dataclass(frozen=True)
class CursorFrame:
    nominal_size: int
    width: int
    height: int
    xhot: int
    yhot: int
    bgra_pixels: bytes


@dataclass(frozen=True)
class ThemeInfo:
    name: str
    path: Path
    requested_size: int
    files: dict[str, Path]


def resource_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "assets" / name


def application_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256, 512):
        candidate = resource_path(f"cursober-{size}.png")
        if candidate.is_file():
            icon.addFile(str(candidate), QSize(size, size))
    if icon.isNull():
        fallback = resource_path("cursober.svg")
        if fallback.is_file():
            icon = QIcon(str(fallback))
    return icon


def read_cursor_theme() -> tuple[str, int]:
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(Path.home() / ".config/kcminputrc")
    theme = config.get("Mouse", "cursorTheme", fallback="").strip()
    try:
        size = int(config.get("Mouse", "cursorSize", fallback="24"))
    except ValueError:
        size = 24
    if not theme:
        raise CursoberError("KDE has no cursor theme configured in ~/.config/kcminputrc.")
    return theme, size if size > 0 else 24


def find_theme(name: str, requested_size: int) -> ThemeInfo:
    locations = (
        Path.home() / ".local/share/icons",
        Path.home() / ".icons",
        Path("/usr/local/share/icons"),
        Path("/usr/share/icons"),
    )
    for location in locations:
        candidate = location / name
        if (candidate / "cursors").is_dir() and (candidate / "index.theme").is_file():
            available = {file.name: file for file in (candidate / "cursors").iterdir() if file.is_file()}
            return ThemeInfo(name, candidate, requested_size, available)
    raise CursoberError(f"The active cursor theme '{name}' was not found in the standard icon directories.")


def xcursor_frames(path: Path) -> list[CursorFrame]:
    data = path.read_bytes()
    if len(data) < 16:
        raise CursoberError(f"{path.name} is too small to be an Xcursor file.")
    magic, header_size, _version, count = struct.unpack_from("<4I", data)
    if magic != 0x72756358:
        raise CursoberError(f"{path.name} is not an Xcursor file.")
    if header_size + count * 12 > len(data):
        raise CursoberError(f"{path.name} has an invalid Xcursor table of contents.")
    frames: list[CursorFrame] = []
    for index in range(count):
        chunk_type, nominal_size, position = struct.unpack_from("<3I", data, header_size + index * 12)
        if chunk_type != IMAGE_TYPE or position + 36 > len(data):
            continue
        _chunk_size, _kind, _subtype, _chunk_version, width, height, xhot, yhot, _delay = struct.unpack_from(
            "<9I", data, position
        )
        pixels_end = position + 36 + width * height * 4
        if not width or not height or pixels_end > len(data):
            continue
        frames.append(CursorFrame(nominal_size, width, height, xhot, yhot, data[position + 36 : pixels_end]))
    if not frames:
        raise CursoberError(f"{path.name} contains no readable image frame.")
    return frames


def choose_frame(path: Path, preferred_size: int) -> CursorFrame:
    frames = xcursor_frames(path)
    fitting = [frame for frame in frames if frame.width <= TEXTURE_SIZE and frame.height <= TEXTURE_SIZE]
    candidates = fitting or frames
    return min(candidates, key=lambda frame: (abs(frame.nominal_size - preferred_size), frame.nominal_size))


def frame_image(frame: CursorFrame) -> QImage:
    image = QImage(frame.width, frame.height, QImage.Format.Format_RGBA8888)
    for y in range(frame.height):
        for x in range(frame.width):
            offset = (y * frame.width + x) * 4
            blue, green, red, alpha = frame.bgra_pixels[offset : offset + 4]
            image.setPixelColor(x, y, QColor(red, green, blue, alpha))
    return image


def texture_from_frame(frame: CursorFrame) -> QImage:
    source = frame_image(frame)
    xhot, yhot = frame.xhot, frame.yhot
    if source.width() > TEXTURE_SIZE or source.height() > TEXTURE_SIZE:
        scale = min(TEXTURE_SIZE / source.width(), TEXTURE_SIZE / source.height())
        source = source.scaled(
            round(source.width() * scale), round(source.height() * scale), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        xhot, yhot = round(xhot * scale), round(yhot * scale)
    output = QImage(TEXTURE_SIZE, TEXTURE_SIZE, QImage.Format.Format_RGBA8888)
    output.fill(Qt.GlobalColor.transparent)
    painter = QPainter(output)
    painter.drawImage(TEXTURE_SIZE // 2 - xhot, TEXTURE_SIZE // 2 - yhot, source)
    painter.end()
    return output


class CursorService:
    def __init__(self) -> None:
        self.sober_data = Path.home() / f".var/app/{SOBER_ID}/data/sober"
        self.overlay = self.sober_data / "asset_overlay/content/textures/Cursors/KeyboardMouse"
        self.backups = self.sober_data / "cursober-backups"

    def inspect(self) -> tuple[ThemeInfo, dict[str, tuple[str, Path, CursorFrame]]]:
        name, size = read_cursor_theme()
        theme = find_theme(name, size)
        selected: dict[str, tuple[str, Path, CursorFrame]] = {}
        for target, (_label, candidates) in TARGETS.items():
            source_name = next((candidate for candidate in candidates if candidate in theme.files), None)
            if source_name is None:
                raise CursoberError(f"{theme.name} has no usable cursor for {target}: tried {', '.join(candidates)}.")
            source = theme.files[source_name]
            selected[target] = (source_name, source, choose_frame(source, theme.requested_size))
        return theme, selected

    def apply(self) -> Path:
        if not self.sober_data.is_dir():
            raise CursoberError("Sober data was not found. Install and launch the Flatpak once before applying an overlay.")
        _theme, selected = self.inspect()
        self.overlay.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
        transaction = self.backups / stamp
        transaction.mkdir(parents=True, exist_ok=False)
        manifest: dict[str, bool] = {}
        for target in TARGETS:
            existing = self.overlay / target
            manifest[target] = existing.is_file()
            if existing.is_file():
                shutil.copy2(existing, transaction / target)
        (transaction / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        try:
            for target, (_source_name, _source, frame) in selected.items():
                temporary = self.overlay / f".{target}.tmp"
                if not texture_from_frame(frame).save(str(temporary), "PNG"):
                    raise CursoberError(f"Could not write {target}.")
                os.replace(temporary, self.overlay / target)
        except Exception:
            for target, existed in manifest.items():
                destination = self.overlay / target
                backup = transaction / target
                if existed and backup.exists():
                    shutil.copy2(backup, destination)
                elif destination.exists():
                    destination.unlink()
            raise
        return transaction

    def restore_latest(self) -> Path:
        transactions = sorted((item for item in self.backups.glob("*") if (item / "manifest.json").is_file()), reverse=True)
        if not transactions:
            raise CursoberError("No Cursober backup is available to restore.")
        transaction = transactions[0]
        manifest = json.loads((transaction / "manifest.json").read_text(encoding="utf-8"))
        self.overlay.mkdir(parents=True, exist_ok=True)
        for target, existed in manifest.items():
            destination = self.overlay / target
            backup = transaction / target
            if existed:
                if not backup.is_file():
                    raise CursoberError(f"Backup {backup} is missing; no files were changed.")
                shutil.copy2(backup, destination)
            elif destination.exists():
                destination.unlink()
        return transaction


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.service = CursorService()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(application_icon())
        self.setMinimumWidth(620)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        title = QLabel("Cursober")
        title.setObjectName("title")
        subtitle = QLabel("Use your active Linux cursor theme for Sober's supported Roblox cursor overlay.")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setHorizontalSpacing(18)
        self.theme_value = QLabel()
        self.mapping_value = QLabel()
        self.overlay_value = QLabel(str(self.service.overlay))
        self.overlay_value.setWordWrap(True)
        for row, (label, value) in enumerate((("Active theme", self.theme_value), ("Cursor mapping", self.mapping_value), ("Sober overlay", self.overlay_value))):
            heading = QLabel(label)
            heading.setObjectName("heading")
            grid.addWidget(heading, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
        layout.addWidget(card)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.apply_button = QPushButton("Apply to Sober")
        self.apply_button.setDefault(True)
        self.restore_button = QPushButton("Restore latest backup")
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        actions.addWidget(self.restore_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

        note = QLabel("Cursober never changes your desktop theme. Applying creates a timestamped backup; restart Sober for changes to take effect.")
        note.setWordWrap(True)
        note.setObjectName("note")
        layout.addWidget(note)
        self.setCentralWidget(root)
        self.setStyleSheet("#title { font-size: 28px; font-weight: 700; } #heading { font-weight: 600; } #note { color: palette(mid); }")
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_button.clicked.connect(self.apply)
        self.restore_button.clicked.connect(self.restore)
        self.refresh()

    def refresh(self) -> None:
        try:
            theme, selected = self.service.inspect()
            mapping = ", ".join(f"{TARGETS[target][0]} → {source}" for target, (source, _path, _frame) in selected.items())
            self.theme_value.setText(f"{theme.name} ({theme.requested_size}px)")
            self.mapping_value.setText(mapping)
            self.status.setText("Ready. The 64×64 Roblox textures preserve the source hotspot at their center.")
            self.apply_button.setEnabled(self.service.sober_data.is_dir())
        except CursoberError as error:
            self.theme_value.setText("Unavailable")
            self.mapping_value.setText("Unavailable")
            self.status.setText(str(error))
            self.apply_button.setEnabled(False)

    def apply(self) -> None:
        try:
            backup = self.service.apply()
            self.status.setText(f"Applied. Backup: {backup}. Restart Sober to load the overlay.")
        except CursoberError as error:
            QMessageBox.critical(self, APP_NAME, str(error))
        except OSError as error:
            QMessageBox.critical(self, APP_NAME, f"Could not apply the cursor overlay: {error}")

    def restore(self) -> None:
        try:
            backup = self.service.restore_latest()
            self.status.setText(f"Restored {backup}. Restart Sober to load the restored files.")
        except CursoberError as error:
            QMessageBox.information(self, APP_NAME, str(error))
        except OSError as error:
            QMessageBox.critical(self, APP_NAME, f"Could not restore the backup: {error}")


def main() -> int:
    service = CursorService()
    if "--self-test" in sys.argv:
        theme, selected = service.inspect()
        print(json.dumps({"theme": theme.name, "size": theme.requested_size, "mapping": {target: source for target, (source, _path, _frame) in selected.items()}}, indent=2))
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(application_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
