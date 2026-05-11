# VRC Asset Organizer

A desktop asset librarian for VRChat avatar creators. Drag in .zip, .unitypackage, .blend, .fbx, or .png files and it scans them, extracts preview thumbnails, auto-tags from filenames, and lets you search, filter, and organize everything.

Not a 3D viewer. Not a Unity replacement. It's a file cabinet that understands what's inside your asset packs.

## What it does

- **Import** — drag files or folders onto the window. Archives get extracted. Every file gets tagged by type, contents, and what avatar or body part it's for.
- **Browse** — thumbnail grid with lazy loading. See previews at a glance.
- **Filter** — genre chips (Avatar Base, Outfit & Acce, Gimmick, Tools), avatar picker, body map, and tag chips. Mix and match.
- **Inspect** — click any asset to see its contents tree, metadata, tags, and notes. Launch it in Blender or Unity.
- **Open With** — send an asset straight to a running Unity Editor. The included Unity plugin imports it into the right genre folder automatically.

## Install

1. Download the zip from [Releases](https://github.com/Mozieyo/VRCAssetOrganizer/releases).
2. Unzip anywhere.
3. Run `VrcAssetOrganizer.exe`.

No installer. Writes data to `%LOCALAPPDATA%\VrcAssetOrganizer\`.

## Run from source

```
cd VrcAssetOrganizer
pip install PySide6
PYTHONPATH="." python vrc_organizer/main.py
```

## Unity plugin

Drop the `UnityPlugin` folder into your Unity project's `Packages/` directory, or add it via Package Manager → "Add package from disk" pointing to `UnityPlugin/package.json`.

When you click "Open With → Unity Editor" in the app, the plugin imports the asset into the right genre folder and generates a preview thumbnail. Configure genre folders under **Window → VRC Thumbnail Settings**.

## Tech

Python 3.11+, PySide6, SQLite. Windows only.
