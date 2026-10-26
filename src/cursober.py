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
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Cursober"
SOBER_ID = "org.vinegarhq.Sober"
IMAGE_TYPE = 0xFFFD0002
TEXTURE_SIZE = 64
TARGETS = {
    "ArrowFarCursor.png": ("Normal", ("left_ptr", "default", "arrow", "X_cursor")),
    "ArrowCursor.png": ("Interactive", ("hand2", "link", "pointer", "hand1")),
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
        self.setMinimumSize(860, 590)
        self.resize(960, 640)

        root = QWidget()
        root.setObjectName("root")
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(218)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 22, 18, 18)
        sidebar_layout.setSpacing(8)
        brand = QLabel("Cursober")
        brand.setObjectName("brand")
        subtitle = QLabel("Sober cursor tools")
        subtitle.setObjectName("brandSubtitle")
        sidebar_layout.addWidget(brand)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addSpacing(28)
        nav_label = QLabel("WORKSPACE")
        nav_label.setObjectName("navLabel")
        sidebar_layout.addWidget(nav_label)
        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.pages = QStackedWidget()
        for index, label in enumerate(("Overview", "Cursor overlay", "About")):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, page=index: self.pages.setCurrentIndex(page))
            self.navigation.addButton(button)
            sidebar_layout.addWidget(button)
            if index == 0:
                button.setChecked(True)
        sidebar_layout.addStretch()
        sidebar_note = QLabel("Your desktop cursor stays untouched.")
        sidebar_note.setObjectName("sidebarNote")
        sidebar_note.setWordWrap(True)
        sidebar_layout.addWidget(sidebar_note)
        shell.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(34, 30, 34, 22)
        content_layout.setSpacing(16)
        self.pages.addWidget(self._overview_page())
        self.pages.addWidget(self._overlay_page())
        self.pages.addWidget(self._about_page())
        content_layout.addWidget(self.pages, 1)
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        content_layout.addWidget(self.status)
        shell.addWidget(content, 1)
        self.setCentralWidget(root)
        self._apply_style()
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_button.clicked.connect(self.apply)
        self.quick_apply_button.clicked.connect(self.apply)
        self.restore_button.clicked.connect(self.restore)
        self.refresh()

    @staticmethod
    def _page_header(title: str, description: str) -> QWidget:
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        description_label = QLabel(description)
        description_label.setObjectName("pageDescription")
        description_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        return header

    @staticmethod
    def _card(title: str, detail: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        if detail:
            description = QLabel(detail)
            description.setObjectName("cardDetail")
            description.setWordWrap(True)
            layout.addWidget(description)
        return card, layout

    def _overview_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(26, 24, 26, 24)
        hero_layout.setSpacing(8)
        eyebrow = QLabel("ROBLOX CURSOR OVERLAY")
        eyebrow.setObjectName("eyebrow")
        hero_title = QLabel("Bring your Linux cursor\ninto Roblox.")
        hero_title.setObjectName("heroTitle")
        hero_body = QLabel("Cursober converts the right Xcursor artwork and installs it through Sober's supported asset overlay.")
        hero_body.setObjectName("heroBody")
        hero_body.setWordWrap(True)
        self.quick_apply_button = QPushButton("Apply cursor overlay")
        self.quick_apply_button.setObjectName("primaryButton")
        self.quick_apply_button.setCursor(Qt.CursorShape.PointingHandCursor)
        hero_layout.addWidget(eyebrow)
        hero_layout.addWidget(hero_title)
        hero_layout.addWidget(hero_body)
        hero_layout.addSpacing(6)
        hero_layout.addWidget(self.quick_apply_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(hero)
        layout.addWidget(self._page_header("At a glance", "Cursober reads your current KDE cursor setting whenever you refresh."))
        cards = QHBoxLayout()
        cards.setSpacing(12)
        theme_card, theme_layout = self._card("ACTIVE THEME")
        self.theme_value = QLabel()
        self.theme_value.setObjectName("cardValue")
        self.theme_value.setWordWrap(True)
        theme_layout.addWidget(self.theme_value)
        sober_card, sober_layout = self._card("SOBER")
        self.sober_value = QLabel()
        self.sober_value.setObjectName("cardValue")
        sober_layout.addWidget(self.sober_value)
        mapping_card, mapping_layout = self._card("CURSOR STATES")
        self.mapping_value = QLabel()
        self.mapping_value.setObjectName("cardValue")
        self.mapping_value.setWordWrap(True)
        mapping_layout.addWidget(self.mapping_value)
        for card in (theme_card, sober_card, mapping_card):
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        layout.addStretch()
        return page

    def _overlay_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header("Cursor overlay", "Review the two Roblox textures Cursober can replace safely."))
        mappings = QHBoxLayout()
        mappings.setSpacing(12)
        normal_card, normal_layout = self._card("NORMAL CURSOR", "Used away from buttons and interactive UI.")
        self.normal_mapping = QLabel()
        self.normal_mapping.setObjectName("mappingValue")
        self.normal_mapping.setWordWrap(True)
        normal_layout.addWidget(self.normal_mapping)
        interactive_card, interactive_layout = self._card("INTERACTIVE CURSOR", "Used over supported buttons and links.")
        self.interactive_mapping = QLabel()
        self.interactive_mapping.setObjectName("mappingValue")
        self.interactive_mapping.setWordWrap(True)
        interactive_layout.addWidget(self.interactive_mapping)
        mappings.addWidget(normal_card, 1)
        mappings.addWidget(interactive_card, 1)
        layout.addLayout(mappings)
        overlay_card, overlay_layout = self._card("SOBER OVERLAY LOCATION")
        self.overlay_value = QLabel(str(self.service.overlay))
        self.overlay_value.setObjectName("pathValue")
        self.overlay_value.setWordWrap(True)
        overlay_layout.addWidget(self.overlay_value)
        layout.addWidget(overlay_card)
        safety = QLabel("Applying creates a timestamped backup first. Cursober writes only its two overlay textures and never changes your KDE cursor theme.")
        safety.setObjectName("safetyNote")
        safety.setWordWrap(True)
        layout.addWidget(safety)
        layout.addStretch()
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh detection")
        self.restore_button = QPushButton("Restore latest backup")
        self.apply_button = QPushButton("Apply to Sober")
        self.apply_button.setObjectName("primaryButton")
        for button in (self.refresh_button, self.restore_button, self.apply_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        actions.addWidget(self.restore_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        return page

    def _about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._page_header("About Cursober", "A small, reversible bridge between KDE cursor themes and Sober."))
        safety_card, safety_layout = self._card("DESIGNED TO STAY OUT OF THE WAY")
        safety_detail = QLabel("• Reads the active cursor theme from KDE\n• Converts original Xcursor frames to transparent PNG textures\n• Backs up existing Sober overlay files before changing them\n• Does not patch Roblox, Sober, or desktop cursor settings")
        safety_detail.setObjectName("aboutDetail")
        safety_detail.setWordWrap(True)
        safety_layout.addWidget(safety_detail)
        layout.addWidget(safety_card)
        future_card, future_layout = self._card("WHAT'S NEXT", "Cursober is growing into a wider Sober companion.")
        future_detail = QLabel("The new navigation is ready for future Sober tweaks and organized FastFlag profiles, while the cursor overlay stays focused and safe today.")
        future_detail.setObjectName("cardDetail")
        future_detail.setWordWrap(True)
        future_layout.addWidget(future_detail)
        layout.addWidget(future_card)
        layout.addStretch()
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            #root { background: #11121b; color: #f3f0ff; }
            #sidebar { background: #181923; border-right: 1px solid #2b2d3b; }
            #brand { color: #ffffff; font-size: 25px; font-weight: 750; }
            #brandSubtitle, #sidebarNote, #pageDescription, #cardDetail, #safetyNote, #status { color: #aba9be; }
            #brandSubtitle { font-size: 12px; }
            #navLabel, #eyebrow, #cardTitle { color: #918ca8; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
            #navButton { background: transparent; border: 0; border-radius: 9px; color: #bcb9ca; padding: 10px 12px; text-align: left; font-weight: 600; }
            #navButton:hover { background: #262735; color: #ffffff; }
            #navButton:checked { background: #6d4bc1; color: #ffffff; }
            #sidebarNote { background: #21222e; border-radius: 9px; font-size: 12px; padding: 10px; }
            #pageTitle { color: #ffffff; font-size: 28px; font-weight: 750; }
            #pageDescription { font-size: 14px; }
            #hero { background: #29213e; border: 1px solid #4d3b78; border-radius: 16px; }
            #heroTitle { color: #ffffff; font-size: 30px; font-weight: 750; }
            #heroBody { color: #d1c9e8; font-size: 14px; }
            #card { background: #1c1d28; border: 1px solid #2d2e3d; border-radius: 12px; }
            #cardValue { color: #ffffff; font-size: 16px; font-weight: 650; }
            #mappingValue { color: #e4ddfa; font-family: monospace; font-size: 13px; }
            #pathValue { color: #c8c4d8; font-family: monospace; font-size: 12px; }
            #aboutDetail { color: #d3cfdf; line-height: 1.65; }
            #primaryButton { background: #8b5cf6; border: 0; border-radius: 9px; color: #ffffff; font-weight: 700; padding: 10px 16px; }
            #primaryButton:hover { background: #9d75f8; }
            #primaryButton:disabled { background: #4a435d; color: #aaa4b7; }
            QPushButton { background: #262735; border: 1px solid #343544; border-radius: 9px; color: #f2effb; padding: 9px 13px; font-weight: 600; }
            QPushButton:hover { background: #303142; }
            #status { background: #1b1c27; border-radius: 8px; font-size: 12px; padding: 10px 12px; }
        """)

    def refresh(self) -> None:
        try:
            theme, selected = self.service.inspect()
            normal = selected["ArrowFarCursor.png"][0]
            interactive = selected["ArrowCursor.png"][0]
            self.theme_value.setText(f"{theme.name}\n{theme.requested_size}px")
            self.sober_value.setText("Detected" if self.service.sober_data.is_dir() else "Launch Sober once")
            self.mapping_value.setText(f"Normal: {normal}\nInteractive: {interactive}")
            self.normal_mapping.setText(f"{normal}  →  ArrowFarCursor.png")
            self.interactive_mapping.setText(f"{interactive}  →  ArrowCursor.png")
            self.status.setText("Ready. Roblox uses fixed 64×64 textures; Cursober keeps the source cursor hotspot centered in that canvas.")
            enabled = self.service.sober_data.is_dir()
            self.apply_button.setEnabled(enabled)
            self.quick_apply_button.setEnabled(enabled)
        except CursoberError as error:
            self.theme_value.setText("Unavailable")
            self.sober_value.setText("Not ready")
            self.mapping_value.setText("Unavailable")
            self.normal_mapping.setText("Unavailable")
            self.interactive_mapping.setText("Unavailable")
            self.status.setText(str(error))
            self.apply_button.setEnabled(False)
            self.quick_apply_button.setEnabled(False)

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
