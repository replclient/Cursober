#!/usr/bin/env python3
"""veyro: safely apply the active Linux cursor theme to Sober's cursor overlay."""
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

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QSize, Qt, Property
from PySide6.QtGui import QColor, QIcon, QImage, QIntValidator, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QGraphicsOpacityEffect,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "veyro"
SOBER_ID = "org.vinegarhq.Sober"
IMAGE_TYPE = 0xFFFD0002
TEXTURE_SIZE = 64
TARGETS = {
    "ArrowFarCursor.png": ("Normal", ("left_ptr", "default", "arrow", "X_cursor")),
    "ArrowCursor.png": ("Interactive", ("hand2", "link", "pointer", "hand1")),
}

SOBER_CONFIG_HEADER = "// Managed by veyro. Use Sober's Settings menu or veyro to change these values.\n\n"
FLAG_GROUPS = {
    "Geometry": (
        ("DFIntCSGLevelOfDetailSwitchingDistance", "CSG distance", 0, 1000),
        ("DFIntCSGLevelOfDetailSwitchingDistanceL12", "CSG distance · quality 1–2", 0, 1000),
        ("DFIntCSGLevelOfDetailSwitchingDistanceL23", "CSG distance · quality 2–3", 0, 1000),
        ("DFIntCSGLevelOfDetailSwitchingDistanceL34", "CSG distance · quality 3–4", 0, 1000),
    ),
    "Rendering": (
        ("DFFlagTextureQualityOverrideEnabled", "Enable texture-quality override"),
        ("DFIntTextureQualityOverride", "Texture quality", 0, 3),
        ("FIntDebugForceMSAASamples", "MSAA samples", 1, 4),
        ("FFlagDebugSkyGray", "Use a gray sky"),
        ("DFFlagDebugPauseVoxelizer", "Disable voxel lighting"),
        ("DFIntDebugFRMQualityLevelOverride", "Graphics quality", 0, 21),
        ("FIntFRMMaxGrassDistance", "Maximum grass distance", 0, 1000),
        ("FIntFRMMinGrassDistance", "Minimum grass distance", 0, 1000),
    ),
    "Accessibility": (
        ("FIntGrassMovementReducedMotionFactor", "Reduce grass movement"),
    ),
}


class VeyroError(RuntimeError):
    pass


def blend(first: QColor, second: QColor, progress: float) -> QColor:
    return QColor(
        round(first.red() + (second.red() - first.red()) * progress),
        round(first.green() + (second.green() - first.green()) * progress),
        round(first.blue() + (second.blue() - first.blue()) * progress),
    )


class ToggleSwitch(QCheckBox):
    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self._position = 1.0 if self.isChecked() else 0.0
        self.animation = QPropertyAnimation(self, b"position", self)
        self.animation.setDuration(170)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _animate(self, checked: bool) -> None:
        self.animation.stop()
        self.animation.setStartValue(self._position)
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()

    def get_position(self) -> float:
        return self._position

    def set_position(self, value: float) -> None:
        self._position = value
        self.update()

    position = Property(float, get_position, set_position)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRect(0, (self.height() - 20) // 2, 36, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(blend(QColor("#343434"), QColor("#3478e5"), self._position))
        painter.drawRoundedRect(track, 10, 10)
        knob_x = track.x() + 2 + round(16 * self._position)
        painter.setBrush(QColor("#eef1fa"))
        painter.drawEllipse(knob_x, track.y() + 2, 16, 16)
        painter.setPen(QColor("#d1d5e3"))
        painter.drawText(QRect(46, 0, self.width() - 46, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


class NavButton(QPushButton):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self._progress = 0.0
        self.animation = QPropertyAnimation(self, b"selectionProgress", self)
        self.animation.setDuration(180)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self.animation.stop()
        self.animation.setStartValue(self._progress)
        self.animation.setEndValue(1.0 if checked else 0.0)
        self.animation.start()

    def get_selection_progress(self) -> float:
        return self._progress

    def set_selection_progress(self, value: float) -> None:
        self._progress = value
        self.update()

    selectionProgress = Property(float, get_selection_progress, set_selection_progress)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._progress:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(89, 112, 205, round(230 * self._progress)))
            painter.drawRoundedRect(0, 8, 2, self.height() - 16, 1, 1)
        color = blend(QColor("#aeb4c8"), QColor("#edf0f8"), self._progress)
        painter.setPen(color)
        font = self.font()
        font.setWeight(700 if self.isChecked() else 500)
        painter.setFont(font)
        painter.drawText(QRect(11, 0, self.width() - 11, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text())


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
    logo = resource_path("veyro.svg")
    return QIcon(str(logo)) if logo.is_file() else QIcon()


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
        raise VeyroError("KDE has no cursor theme configured in ~/.config/kcminputrc.")
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
    raise VeyroError(f"The active cursor theme '{name}' was not found in the standard icon directories.")


def xcursor_frames(path: Path) -> list[CursorFrame]:
    data = path.read_bytes()
    if len(data) < 16:
        raise VeyroError(f"{path.name} is too small to be an Xcursor file.")
    magic, header_size, _version, count = struct.unpack_from("<4I", data)
    if magic != 0x72756358:
        raise VeyroError(f"{path.name} is not an Xcursor file.")
    if header_size + count * 12 > len(data):
        raise VeyroError(f"{path.name} has an invalid Xcursor table of contents.")
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
        raise VeyroError(f"{path.name} contains no readable image frame.")
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
        self.backups = self.sober_data / "veyro-backups"
        self.legacy_backups = self.sober_data / "cursober-backups"

    def inspect(self) -> tuple[ThemeInfo, dict[str, tuple[str, Path, CursorFrame]]]:
        name, size = read_cursor_theme()
        theme = find_theme(name, size)
        selected: dict[str, tuple[str, Path, CursorFrame]] = {}
        for target, (_label, candidates) in TARGETS.items():
            source_name = next((candidate for candidate in candidates if candidate in theme.files), None)
            if source_name is None:
                raise VeyroError(f"{theme.name} has no usable cursor for {target}: tried {', '.join(candidates)}.")
            source = theme.files[source_name]
            selected[target] = (source_name, source, choose_frame(source, theme.requested_size))
        return theme, selected

    def apply(self) -> Path:
        if not self.sober_data.is_dir():
            raise VeyroError("Sober data was not found. Install and launch the Flatpak once before applying an overlay.")
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
                    raise VeyroError(f"Could not write {target}.")
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
        transactions = sorted(
            (item for root in (self.backups, self.legacy_backups) for item in root.glob("*") if (item / "manifest.json").is_file()),
            key=lambda item: item.name,
            reverse=True,
        )
        if not transactions:
            raise VeyroError("No veyro backup is available to restore.")
        transaction = transactions[0]
        manifest = json.loads((transaction / "manifest.json").read_text(encoding="utf-8"))
        self.overlay.mkdir(parents=True, exist_ok=True)
        for target, existed in manifest.items():
            destination = self.overlay / target
            backup = transaction / target
            if existed:
                if not backup.is_file():
                    raise VeyroError(f"Backup {backup} is missing; no files were changed.")
                shutil.copy2(backup, destination)
            elif destination.exists():
                destination.unlink()
        return transaction


class SoberConfigService:
    def __init__(self) -> None:
        self.path = Path.home() / f".var/app/{SOBER_ID}/config/sober/config.json"
        self.backups = Path.home() / f".var/app/{SOBER_ID}/data/sober/veyro-backups/settings"

    def load(self) -> dict:
        if not self.path.is_file():
            raise VeyroError("Sober's configuration was not found. Launch Sober once, then return here.")
        try:
            content = "\n".join(line for line in self.path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("//"))
            config = json.loads(content)
        except (OSError, json.JSONDecodeError) as error:
            raise VeyroError(f"Sober's configuration could not be read: {error}") from error
        if not isinstance(config, dict):
            raise VeyroError("Sober's configuration has an invalid format.")
        return config

    def save(self, config: dict) -> Path:
        if not isinstance(config.get("fflags", {}), dict):
            raise VeyroError("Sober's FastFlags entry has an invalid format; nothing was changed.")
        self.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S.%fZ")
        backup = self.backups / f"config-{stamp}.json"
        shutil.copy2(self.path, backup)
        temporary = self.path.with_suffix(".json.tmp")
        try:
            temporary.write_text(SOBER_CONFIG_HEADER + json.dumps(config, indent=4, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        return backup


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.service = CursorService()
        self.config_service = SoberConfigService()
        self.flag_widgets: dict[str, QCheckBox | QComboBox | QLineEdit] = {}
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(application_icon())
        self.setMinimumSize(720, 560)
        self.resize(820, 640)

        root = QWidget()
        root.setObjectName("root")
        shell = QVBoxLayout(root)
        shell.setContentsMargins(28, 26, 28, 20)
        shell.setSpacing(14)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(11)
        logo = QLabel()
        logo.setFixedSize(QSize(38, 38))
        logo.setPixmap(application_icon().pixmap(QSize(38, 38)))
        brand = QLabel("veyro")
        brand.setObjectName("brand")
        subtitle = QLabel("Sober toolkit")
        subtitle.setObjectName("brandSubtitle")
        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(0)
        brand_text.addWidget(brand)
        brand_text.addWidget(subtitle)
        brand_row.addWidget(logo)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        shell.addLayout(brand_row)

        self.navigation = QButtonGroup(self)
        self.navigation.setExclusive(True)
        self.pages = QStackedWidget()
        navigation_row = QHBoxLayout()
        navigation_row.setContentsMargins(0, 2, 0, 4)
        navigation_row.setSpacing(18)
        for index, label in enumerate(("Cursor overlay", "Sober settings", "FastFlags", "Backups", "About")):
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, page=index: self.show_page(page))
            self.navigation.addButton(button)
            navigation_row.addWidget(button)
            if index == 0:
                button.setChecked(True)
        navigation_row.addStretch()
        shell.addLayout(navigation_row)

        self.pages.addWidget(self._scroll_page(self._overview_page()))
        self.pages.addWidget(self._scroll_page(self._settings_page()))
        self.pages.addWidget(self._scroll_page(self._fastflags_page()))
        self.pages.addWidget(self._scroll_page(self._overlay_page()))
        self.pages.addWidget(self._scroll_page(self._about_page()))
        shell.addWidget(self.pages, 1)
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        shell.addWidget(self.status)
        self.setCentralWidget(root)
        self._apply_style()
        self.refresh_button.clicked.connect(self.refresh)
        self.apply_button.clicked.connect(self.apply)
        self.restore_cursor_button.clicked.connect(self.restore)
        self.restore_button.clicked.connect(self.restore)
        self.save_settings_button.clicked.connect(self.save_settings)
        self.save_flags_button.clicked.connect(self.save_fastflags)
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
    def _scroll_page(page: QWidget) -> QScrollArea:
        page.setObjectName("page")
        page.setAutoFillBackground(False)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setObjectName("pageViewport")
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _card(title: str, detail: str = "") -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
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
        layout.addWidget(self._page_header("Cursor overlay", "Use your active KDE cursor theme for Sober's two supported Roblox cursor textures."))
        details = QGridLayout()
        details.setHorizontalSpacing(24)
        details.setVerticalSpacing(7)
        self.theme_value = QLabel()
        self.mapping_value = QLabel()
        self.overlay_value = QLabel(str(self.service.overlay))
        for row, (label_text, value) in enumerate((("Active theme", self.theme_value), ("Cursor mapping", self.mapping_value), ("Sober overlay", self.overlay_value))):
            label = QLabel(label_text)
            label.setObjectName("fieldLabel")
            details.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            value.setObjectName("fieldValue" if row < 2 else "pathValue")
            details.addWidget(value, row, 1)
        self.overlay_value.setWordWrap(True)
        details.setColumnStretch(1, 1)
        layout.addLayout(details)

        note = QLabel("veyro creates a timestamped backup before applying changes. Your system cursor theme is never modified.")
        note.setObjectName("safetyNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.restore_cursor_button = QPushButton("Restore latest backup")
        self.apply_button = QPushButton("Apply to Sober")
        self.apply_button.setDefault(True)
        self.apply_button.setObjectName("primaryButton")
        for button in (self.refresh_button, self.restore_cursor_button, self.apply_button):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        actions.addWidget(self.restore_cursor_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        layout.addStretch()
        return page

    def _settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header("Sober settings", "Documented Sober options, saved safely with a dated configuration backup."))

        graphics, graphics_layout = self._card("GRAPHICS")
        graphics_grid = QGridLayout()
        graphics_grid.setContentsMargins(0, 8, 0, 0)
        graphics_grid.setHorizontalSpacing(28)
        graphics_grid.setVerticalSpacing(9)
        graphics_label = QLabel("Optimization mode")
        graphics_label.setObjectName("fieldLabel")
        self.graphics_mode = QComboBox()
        self.graphics_mode.addItems(("quality", "balanced", "performance"))
        graphics_grid.addWidget(graphics_label, 0, 0)
        graphics_grid.addWidget(self.graphics_mode, 0, 1)
        self.hidpi = ToggleSwitch("Use HiDPI window scaling")
        self.gamemode = ToggleSwitch("Enable GameMode when available")
        graphics_grid.addWidget(self.hidpi, 1, 0, 1, 2)
        graphics_grid.addWidget(self.gamemode, 2, 0, 1, 2)
        graphics_layout.addLayout(graphics_grid)
        layout.addWidget(graphics)

        interface, interface_layout = self._card("INPUT & INTERFACE")
        interface_grid = QGridLayout()
        interface_grid.setContentsMargins(0, 8, 0, 0)
        interface_grid.setHorizontalSpacing(28)
        interface_grid.setVerticalSpacing(9)
        touch_label = QLabel("Touch mode")
        touch_label.setObjectName("fieldLabel")
        self.touch_mode = QComboBox()
        self.touch_mode.addItems(("off", "on", "fake-off"))
        interface_grid.addWidget(touch_label, 0, 0)
        interface_grid.addWidget(self.touch_mode, 0, 1)
        self.mobile_home = ToggleSwitch("Use the mobile home screen")
        self.console_experience = ToggleSwitch("Use the console experience")
        self.server_location = ToggleSwitch("Show server location when joining")
        interface_grid.addWidget(self.mobile_home, 1, 0, 1, 2)
        interface_grid.addWidget(self.console_experience, 2, 0, 1, 2)
        interface_grid.addWidget(self.server_location, 3, 0, 1, 2)
        interface_layout.addLayout(interface_grid)
        layout.addWidget(interface)

        integrations, integrations_layout = self._card("INTEGRATIONS")
        self.discord_rpc = ToggleSwitch("Enable Discord Rich Presence")
        self.discord_join = ToggleSwitch("Show Discord join button")
        integrations_layout.addWidget(self.discord_rpc)
        integrations_layout.addWidget(self.discord_join)
        layout.addWidget(integrations)
        layout.addStretch()
        actions = QHBoxLayout()
        self.save_settings_button = QPushButton("Save settings")
        self.save_settings_button.setObjectName("primaryButton")
        self.save_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        actions.addStretch()
        actions.addWidget(self.save_settings_button)
        layout.addLayout(actions)
        return page

    def _fastflags_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header("FastFlags", "A focused editor for Sober's documented, currently allowlisted local flags. Other flags are left untouched."))
        for title, flags in FLAG_GROUPS.items():
            section, section_layout = self._card(title)
            flag_layout = QGridLayout()
            flag_layout.setContentsMargins(0, 8, 0, 0)
            flag_layout.setHorizontalSpacing(28)
            flag_layout.setVerticalSpacing(9)
            for row, flag in enumerate(flags):
                name, label, *limits = flag
                label_widget = QLabel(label)
                label_widget.setObjectName("fieldLabel")
                flag_layout.addWidget(label_widget, row, 0)
                if limits:
                    if name == "FIntDebugForceMSAASamples":
                        editor = QComboBox()
                        editor.addItems(("1", "2", "4"))
                    else:
                        editor = QLineEdit()
                        editor.setValidator(QIntValidator(limits[0], limits[1], editor))
                        editor.setText(str(limits[0]))
                    editor.setObjectName("flagSpin")
                else:
                    editor = ToggleSwitch()
                self.flag_widgets[name] = editor
                flag_layout.addWidget(editor, row, 1)
            flag_layout.setColumnStretch(1, 1)
            section_layout.addLayout(flag_layout)
            layout.addWidget(section)
        note = QLabel("Changes apply when Sober next launches. Values not supported by Roblox's allowlist may be ignored by the client.")
        note.setObjectName("safetyNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.save_flags_button = QPushButton("Save FastFlags")
        self.save_flags_button.setObjectName("primaryButton")
        self.save_flags_button.setCursor(Qt.CursorShape.PointingHandCursor)
        actions.addStretch()
        actions.addWidget(self.save_flags_button)
        layout.addLayout(actions)
        return page

    def _overlay_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header("Backups", "Each apply operation saves the two existing Sober cursor textures before replacing them."))
        backup_card, backup_layout = self._card("RESTORE THE LATEST BACKUP")
        explanation = QLabel("Restore returns the latest pair of files saved by veyro. Restart Sober afterwards to reload the cursor overlay.")
        explanation.setObjectName("cardDetail")
        explanation.setWordWrap(True)
        backup_layout.addWidget(explanation)
        backup_layout.addSpacing(8)
        backup_path = QLabel(str(self.service.backups))
        backup_path.setObjectName("pathValue")
        backup_path.setWordWrap(True)
        backup_layout.addWidget(backup_path)
        layout.addWidget(backup_card)
        note = QLabel("If no veyro backup exists yet, restore will not change any files.")
        note.setObjectName("safetyNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        actions = QHBoxLayout()
        self.restore_button = QPushButton("Restore latest backup")
        self.restore_button.setObjectName("primaryButton")
        self.restore_button.setCursor(Qt.CursorShape.PointingHandCursor)
        actions.addStretch()
        actions.addWidget(self.restore_button)
        layout.addLayout(actions)
        return page

    def _about_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._page_header("About veyro", "A small utility for keeping Sober's base Roblox cursors aligned with your KDE theme."))
        safety_card, safety_layout = self._card("HOW IT WORKS")
        safety_detail = QLabel("• Reads the active cursor theme from KDE\n• Converts original Xcursor frames to transparent PNG textures\n• Backs up existing Sober overlay files before changing them\n• Does not patch Roblox, Sober, or desktop cursor settings")
        safety_detail.setObjectName("aboutDetail")
        safety_detail.setWordWrap(True)
        safety_layout.addWidget(safety_detail)
        layout.addWidget(safety_card)
        layout.addStretch()
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            #root { background: #242424; color: #e2e2e2; }
            #pageScroll, #pageViewport, #page { background: transparent; border: 0; }
            #brand { color: #f0f1f5; font-size: 24px; font-weight: 750; }
            #brandSubtitle { color: #a8a8a8; font-size: 12px; }
            #sidebarNote, #pageDescription, #cardDetail, #safetyNote, #status { color: #ababab; }
            #navLabel, #cardTitle, #tableHeader { color: #9d9d9d; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
            #navButton { background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; color: #bcbcbc; padding: 7px 1px; text-align: left; font-weight: 500; }
            #navButton:hover { color: #f0f0f0; }
            #navButton:checked { background: transparent; border: 0; border-bottom: 2px solid #4085f4; border-radius: 0; color: #ffffff; font-weight: 700; }
            #pageTitle { color: #eeeeee; font-size: 25px; font-weight: 750; }
            #pageDescription { font-size: 14px; }
            #card { background: transparent; border: 0; border-radius: 0; }
            #fieldLabel { color: #c9c9c9; font-weight: 600; }
            #fieldValue { color: #e4e4e4; font-weight: 600; }
            #stateLabel { color: #dddddd; font-weight: 600; }
            #mappingValue, #mappingTarget, #pathValue { color: #dedede; font-family: monospace; }
            #pathValue { font-size: 12px; }
            #aboutDetail { color: #d0d0d0; line-height: 1.65; }
            QComboBox, QLineEdit { background: #343434; border: 0; border-radius: 7px; color: #e5e5e5; min-height: 28px; padding: 2px 9px; }
            QComboBox:hover, QLineEdit:hover, QLineEdit:focus { background: #404040; }
            QComboBox::drop-down { border: 0; width: 22px; }
            QComboBox QAbstractItemView { background: #343434; border: 0; color: #e5e5e5; selection-background-color: #4a4a4a; }
            QPushButton { background: #343434; border: 0; border-radius: 8px; color: #e7e7e7; padding: 8px 14px; }
            QPushButton:hover { background: #414141; }
            QPushButton:pressed { background: #2d2d2d; }
            QPushButton:disabled { background: #2d2d2d; color: #868686; }
            #primaryButton { background: #3478e5; border: 0; color: #ffffff; font-weight: 700; }
            #primaryButton:hover { background: #4385ee; }
            #primaryButton:pressed { background: #2c66c2; }
            #status { background: transparent; border: 0; border-radius: 0; font-size: 12px; padding: 9px 0; }
            QScrollBar:vertical { background: transparent; border: 0; width: 10px; margin: 0; }
            QScrollBar::handle:vertical { background: #5a5a5a; border-radius: 4px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

    def show_page(self, index: int) -> None:
        if index == self.pages.currentIndex():
            return
        page = self.pages.widget(index)
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        self.pages.setCurrentIndex(index)
        self.page_animation = QPropertyAnimation(effect, b"opacity", self)
        self.page_animation.setDuration(180)
        self.page_animation.setStartValue(0.0)
        self.page_animation.setEndValue(1.0)
        self.page_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.page_animation.start()

    def _load_config_widgets(self) -> None:
        config = self.config_service.load()
        self.current_config = config
        self.graphics_mode.setCurrentText(str(config.get("graphics_optimization_mode", "balanced")))
        self.hidpi.setChecked(bool(config.get("enable_hidpi", False)))
        self.gamemode.setChecked(bool(config.get("enable_gamemode", False)))
        self.touch_mode.setCurrentText(str(config.get("touch_mode", "off")))
        self.mobile_home.setChecked(bool(config.get("enable_mobile_home_screen", False)))
        self.console_experience.setChecked(bool(config.get("use_console_experience", False)))
        self.server_location.setChecked(bool(config.get("server_location_indicator_enabled", False)))
        self.discord_rpc.setChecked(bool(config.get("discord_rpc_enabled", False)))
        self.discord_join.setChecked(bool(config.get("discord_rpc_show_join_button", False)))
        self.initial_flags = dict(config.get("fflags", {}))
        for name, editor in self.flag_widgets.items():
            value = self.initial_flags.get(name)
            if isinstance(editor, QCheckBox):
                editor.setChecked(value is True)
            elif isinstance(editor, QComboBox):
                editor.setCurrentText(str(value) if value in (1, 2, 4) else "1")
            else:
                editor.setText(str(value if isinstance(value, int) and not isinstance(value, bool) else 0))

    def save_settings(self) -> None:
        try:
            config = self.config_service.load()
            config.update({
                "graphics_optimization_mode": self.graphics_mode.currentText(),
                "enable_hidpi": self.hidpi.isChecked(),
                "enable_gamemode": self.gamemode.isChecked(),
                "touch_mode": self.touch_mode.currentText(),
                "enable_mobile_home_screen": self.mobile_home.isChecked(),
                "use_console_experience": self.console_experience.isChecked(),
                "server_location_indicator_enabled": self.server_location.isChecked(),
                "discord_rpc_enabled": self.discord_rpc.isChecked(),
                "discord_rpc_show_join_button": self.discord_join.isChecked(),
            })
            backup = self.config_service.save(config)
            self.current_config = config
            self.status.setText(f"Saved Sober settings. Backup: {backup}. Restart Sober to apply them.")
        except (VeyroError, OSError) as error:
            QMessageBox.critical(self, APP_NAME, f"Could not save Sober settings: {error}")

    def save_fastflags(self) -> None:
        try:
            config = self.config_service.load()
            flags = dict(config.get("fflags", {}))
            for name, editor in self.flag_widgets.items():
                value = editor.isChecked() if isinstance(editor, QCheckBox) else int(editor.currentText()) if isinstance(editor, QComboBox) else int(editor.text() or "0")
                default = False if isinstance(editor, QCheckBox) else 1 if isinstance(editor, QComboBox) else 0
                if name in self.initial_flags and value != self.initial_flags[name]:
                    flags[name] = value
                elif name not in self.initial_flags and value != default:
                    flags[name] = value
            config["fflags"] = flags
            backup = self.config_service.save(config)
            self.initial_flags = dict(flags)
            self.status.setText(f"Saved FastFlags. Backup: {backup}. Restart Sober to apply them.")
        except (VeyroError, OSError) as error:
            QMessageBox.critical(self, APP_NAME, f"Could not save FastFlags: {error}")

    def refresh(self) -> None:
        try:
            theme, selected = self.service.inspect()
            normal = selected["ArrowFarCursor.png"][0]
            interactive = selected["ArrowCursor.png"][0]
            self.theme_value.setText(f"{theme.name} ({theme.requested_size}px)")
            self.mapping_value.setText(f"Normal → {normal}, Interactive → {interactive}")
            self.status.setText("Ready. Roblox uses fixed 64×64 textures; veyro keeps the source cursor hotspot centered in that canvas.")
            enabled = self.service.sober_data.is_dir()
            self.apply_button.setEnabled(enabled)
        except VeyroError as error:
            self.theme_value.setText("Unavailable")
            self.mapping_value.setText("Unavailable")
            self.status.setText(str(error))
            self.apply_button.setEnabled(False)
        try:
            self._load_config_widgets()
            self.save_settings_button.setEnabled(True)
            self.save_flags_button.setEnabled(True)
        except VeyroError:
            self.save_settings_button.setEnabled(False)
            self.save_flags_button.setEnabled(False)

    def apply(self) -> None:
        try:
            backup = self.service.apply()
            self.status.setText(f"Applied. Backup: {backup}. Restart Sober to load the overlay.")
        except VeyroError as error:
            QMessageBox.critical(self, APP_NAME, str(error))
        except OSError as error:
            QMessageBox.critical(self, APP_NAME, f"Could not apply the cursor overlay: {error}")

    def restore(self) -> None:
        try:
            backup = self.service.restore_latest()
            self.status.setText(f"Restored {backup}. Restart Sober to load the restored files.")
        except VeyroError as error:
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
