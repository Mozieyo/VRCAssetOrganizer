# VRC Asset Organizer

A desktop asset librarian for VRChat avatar creators. Drag in .zip, .unitypackage, .blend, .fbx, .psd, or image files and it indexes them in place, hunts a preview thumbnail out of every archive, auto-tags from filenames + folder structure + readme text, and lets you search, filter, and organize everything.

Not a 3D viewer. Not a Unity replacement. It's a file cabinet that understands what's inside your asset packs.

## What it does

- **Import (in-place)** — drag files or folders onto the window. Your source files stay where you put them; only metadata + thumbnails go into the app's data dir. Archives are read entry-by-entry without unpacking.
- **Browse** — custom-painted thumbnail grid with rubber-band drag-select. Density slider goes from ~10 cards/row to ~3.
- **Filter** — five mutually-exclusive genres (Avatar Base, Outfit, Accessory, Gimmick, Tools), avatar picker, optional body map, and a tag chip cloud. Mix and match.
- **Inspect** — click any asset for a contents tree (with filetype icons), file info, genre + avatar + free tags. The Tags section surfaces suggestions on top of what's already assigned; click to attach.
- **Furigana** — Japanese filenames get a romaji line beside them in the grid and inspector. Toggle under `View → Show Romaji`. Asset search matches romaji too — typing "manuka" finds マヌカ.
- **Tag review** — captcha-style labeling UI for cleaning auto-tags. Space saves & advances. Suggestions grow as you accept chips.
- **Cover labeler** — pick a better preview image from inside an archive. Saves get applied to the thumb cache.
- **Crawler** — `Tools → Crawl Folder for Training Signals`: walks any folder, opens unitypackage/zip archives, reads readme files, mines token co-occurrence. Feeds the autotag pipeline on next import.
- **Shareable training pool** — `Tools → Export Shared Tag Pool`: emit a JSON file with all your tag names + co-occurrence counts. Friends import via the matching menu item; counts add up. Each export has a UUID so re-importing the same file is a safe no-op.

## Install

1. Download the zip from [Releases](https://github.com/Mozieyo/VRCAssetOrganizer/releases).
2. Unzip anywhere.
3. Run `VrcAssetOrganizer.exe`.

No installer. Writes data to `%LOCALAPPDATA%\VrcAssetOrganizer\`:
- `vrc_assets.db` — metadata, tags, labels, co-occurrence
- `thumbnails\*.png` — generated previews

Your source asset files are never moved, copied, or modified.

## Delete vs. remove

- **Delete key / right-click → Remove from Library** — drops the DB row only. The file stays on disk.
- **Right-click → Delete File from Disk…** — also sends the file to the Recycle Bin. Confirms with the full path.
- **Tools → Purge Cache && Packages** — debug nuke. Wipes the DB, thumbnail cache, and the legacy extraction folder. Source files untouched. Re-seeds default tags on next launch.

## Run from source

```
cd BoothOrganizer
pip install PySide6 Pillow send2trash rarfile
PYTHONPATH="." python vrc_organizer/main.py
```

## Unity plugin

The in-app "Import to Unity" button is currently a stub that shells out to Unity Editor with `-importPackage <file>`. The proper Editor plugin (with status round-trip) is a planned follow-up.

## Tech

Python 3.11+, PySide6, SQLite. Windows-first.

## Architecture notes

See `REWRITE_PROPOSAL.md` for the long-form design doc.
