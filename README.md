# veyro

**veyro brings your active Linux cursor theme into Sober's Roblox cursor overlay.**

Sober runs the Android Roblox client and does not expose a supported way to draw the host compositor's cursor directly. It does, however, support an asset overlay for Roblox's built-in cursor textures. veyro turns the relevant Xcursor artwork from your current theme into compatible PNG textures—without changing your desktop theme or modifying Sober itself.

<p align="center"><img src="assets/veyro.svg" width="180" alt="veyro icon"></p>

<p align="center"><img src="assets/veyro-ui.png" width="740" alt="veyro showing the detected KDE theme and Sober cursor mappings"></p>

## What it does

On KDE, veyro reads the cursor theme selected in `~/.config/kcminputrc`, finds that theme in the normal icon directories, and applies these mappings when they exist:

| Roblox overlay | Theme cursor | Purpose |
| --- | --- | --- |
| `ArrowFarCursor.png` | `left_ptr` | Normal cursor |
| `ArrowCursor.png` | `hand2` | Interactive / button hover cursor |

It saves PNGs only to Sober's documented overlay path:

```
~/.var/app/org.vinegarhq.Sober/data/sober/asset_overlay/
└── content/textures/Cursors/KeyboardMouse/
    ├── ArrowCursor.png
    └── ArrowFarCursor.png
```

Before each apply, veyro records the existing overlay files in:

```
~/.var/app/org.vinegarhq.Sober/data/sober/veyro-backups/
```

Use **Restore latest backup** to reverse the most recent apply operation, then restart Sober.

## Sober settings and FastFlags

veyro also exposes a small set of documented Sober settings: graphics optimization, HiDPI and GameMode, touch and interface choices, server location, and Discord Rich Presence.

The FastFlags page groups the local Roblox flags currently documented by Sober into Geometry, Rendering, and Accessibility. It preserves unknown FastFlags already in your configuration and leaves them alone. Roblox ignores flags outside its current allowlist, so veyro deliberately does not present random internet “tweaks.”

Before saving either page, veyro copies Sober's configuration to:

```
~/.var/app/org.vinegarhq.Sober/data/sober/veyro-backups/settings/
```

Restart Sober after changing settings or FastFlags.

## Install and use

1. Download `veyro-x86_64.AppImage` from the latest release.
2. In Dolphin: right-click it → **Properties** → **Permissions** → enable **Is executable**.
3. Open it, confirm the detected theme and cursor mapping, then choose **Apply to Sober**.
4. Restart Sober.

The AppImage does not change the system cursor theme. It only writes the two files above and makes a backup first.

## Requirements

* Linux on x86_64.
* [Sober](https://sober.vinegarhq.org/) installed as Flatpak and launched at least once.
* A KDE/Xcursor theme installed under `~/.local/share/icons`, `~/.icons`, `/usr/local/share/icons`, or `/usr/share/icons`.

## Limitations

* Sober/Roblox currently supplies only the two verified cursor texture slots listed above. Text, loading, resize, and animated cursor states cannot be overlaid safely yet.
* Roblox stores the cursor artwork in a fixed 64×64 canvas. veyro preserves the Xcursor hotspot by aligning it to the canvas center and keeps the original frame size whenever it fits.
* Some Roblox experiences replace their own cursor assets. Those may not use Sober's base cursor textures.

## Build from source

For a local development run:

```bash
python3 -m pip install PySide6
python3 src/veyro.py
```

To check the detected theme without changing anything:

```bash
python3 src/veyro.py --self-test
```

To build the AppImage, install `PySide6`, `PyInstaller`, `rsvg-convert`, and download the official `appimagetool` AppImage. Then run:

```bash
APPIMAGETOOL=/path/to/appimagetool-x86_64.AppImage packaging/build-appimage.sh
```

The build script creates `veyro-x86_64.AppImage` in the project root.

## Credits

Built for [Sober](https://github.com/vinegarhq/sober)'s supported asset-overlay mechanism. The included icon artwork is original veyro artwork.
