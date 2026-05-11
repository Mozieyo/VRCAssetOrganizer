# VRC Thumbnail — Unity Editor Plugin

Generates preview thumbnails and organizes 3D assets sent from the [VRC Asset Organizer](https://github.com/Mozieyo/VRCAssetOrganizer) desktop app.

## How it works

1. You drop an asset onto VRC Asset Organizer, optionally tag it with a genre tag (e.g. "Avatar Base").
2. You click **Open With → Unity Editor**.
3. The desktop app writes asset metadata (path, tags, filetype) to a JSON temp file and launches Unity.
4. Unity runs `VrcThumbnail.ProcessSingle()` which reads the temp file, imports the asset, and routes it to the correct genre folder.
5. `.unitypackage` files — Unity 2022 can't import to a target folder, so the plugin imports to root, then moves everything to the genre folder, flattens single-child folder chains, and selects the imported folder in the Project window.
6. Other file types (.fbx, .blend, etc.) are copied directly to the genre folder and a preview thumbnail is generated via `AssetPreview`.

## Genre folder routing

Each asset in VRC Asset Organizer can be tagged with one or more tags. The plugin matches those tags (case-insensitive) against configured genre names to determine the destination folder.

**Default mapping:**

| Tag match        | Destination folder          |
|------------------|-----------------------------|
| Avatar Base      | Assets/1. Avatar Base       |
| Outfit & Acce    | Assets/2. Outfit & Acce     |
| Gimmick          | Assets/3. Gimmick           |
| Tools            | Assets/4. Tools             |
| (no match)       | Assets/Imported             |

You can customize these in **Window → VRC Thumbnail Settings**.

## Installation

### Via Git URL (once the repo is public)

1. In Unity, open **Window → Package Manager**.
2. Click **+** → **Add package from git URL**.
3. Enter: `https://github.com/Mozieyo/VRCAssetOrganizer.git?path=UnityPlugin`
4. Click **Add**.

### Via disk (local development)

1. In Unity, open **Window → Package Manager**.
2. Click **+** → **Add package from disk**.
3. Select the `UnityPlugin/package.json` file from your local VRCAssetOrganizer clone.

### Manual copy

Copy the `UnityPlugin` folder into your project's `Packages/` directory.

## Requirements

- Unity **2022.3.22f1** or later (the version used by VRChat).
- No external dependencies — uses only Unity's built-in `UnityEditor` namespace.

## Folder flattening

Marketplace asset packs often nest content inside multiple wrapper folders:

```
Assets/
  SomePack_1.0/
    SomePack/
      Textures/
      Models/
```

The plugin detects when a folder contains exactly one child folder and no files, and merges the contents upward, resulting in:

```
Assets/
  1. Avatar Base/
    SomePack_1.0/
      Textures/
      Models/
```

This is applied recursively after every import.

## Preferences

Open **Window → VRC Thumbnail Settings** to configure:

- **Genre → Folder mappings** — which VRC Asset Organizer tags map to which Unity project folders.
- **Default folder** — fallback when no tag matches.
- **Reset to Defaults** — restore the factory mapping.

Settings are stored per-project at `ProjectSettings/VrcThumbnail.json` and can be committed to version control.

## Usage

The plugin is called automatically by VRC Asset Organizer. You can also invoke it manually:

- **Command line (single asset):**
  ```bash
  Unity.exe -projectPath "C:\MyProject" -executeMethod VrcThumbnail.ProcessSingle
  ```
- **Batch mode (all pending):**
  ```bash
  Unity.exe -batchmode -quit -projectPath "C:\MyProject" -executeMethod VrcThumbnail.ProcessAll
  ```

## File locations

| What | Where |
|------|-------|
| Temp file (desktop → Unity) | `%TEMP%\vrc_thumb_{uuid}.txt` (JSON) |
| Import staging (non-package files) | `Assets/VrcImport/` (deleted after processing) |
| Output thumbnail | `{original_dir}\{filename}_preview.png` |
| Settings | `ProjectSettings/VrcThumbnail.json` |

## Troubleshooting

**"No pending asset" dialog on launch** — You clicked Open With without a file selected, or the temp file was cleaned up. Drop the file onto VRC Asset Organizer first.

**Thumbnail timeout** — Complex assets may take longer than 120 frames to generate a preview. The asset is still imported; only the thumbnail save is skipped.

**Package not showing in Package Manager** — Make sure `package.json` has `"type": "tool"` removed if your Unity version doesn't support it (pre-2020.3).

**Assets land in wrong folder** — Check that your VRC Asset Organizer tags match the genre names in **Window → VRC Thumbnail Settings** (case-insensitive match). If no tag matches, the filetype heuristic kicks in: `asset_zip` and `unitypackage` default to the Avatar Base folder.
