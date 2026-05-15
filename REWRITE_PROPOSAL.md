# VRCAssetOrganizer — Rewrite Proposal

**Status:** Draft  
**Scope:** Full architectural rewrite of the core scanning, tagging, and UI systems  
**Preserves:** SQLite, PySide6, Windows-first, Unity plugin contract  
**Replaces:** Filename-dictionary tagging, heuristic thumbnail selection, synchronous scanning

---

## 1. PROBLEM STATEMENT

The current implementation has four structural weaknesses:

1. **Tagging is filename-only.** A package named `v2_FINAL_USETHIS.unitypackage` yields nothing. The richest metadata — Unity's own serialized asset data — is never read.
2. **Thumbnail selection is heuristic and fragile.** "Find an image and hope" fails on the ~40% of packages that contain only a single `.unitypackage` with no loose images.
3. **Scanning is blocking.** Archive reads happen on the main thread or in a way that stalls the UI.
4. **Tags are flat strings.** No confidence, no source audit, no normalization. Japanese creator names and aliases silently fail.

The rewrite fixes all four with a new pipeline and data model. The user-facing features (grid, filter chips, detail panel, Open With Unity) stay. The engine underneath is replaced.

---

## 2. CORE PHILOSOPHY CHANGE

```
CURRENT MODEL          →    REWRITE MODEL
files and folders           semantic asset entities
filename guessing           evidence aggregation
binary tags                 confidence-scored signals
scan-then-display           stream-then-progressively-populate
```

A `.unitypackage` with a useless filename still contains: prefab YAML, blendshape names, shader GUIDs, material definitions, mesh names, and the original Unity project path of every file inside it. The rewrite reads all of that. The filename is a last-resort fallback, not the primary source.

---

## 3. WHAT DOES NOT CHANGE

- Language: Python 3.11+
- UI framework: PySide6
- Database: SQLite (extended, not replaced)
- Storage: Files stay exactly where the user put them. No content-addressed relocation.
- Unity plugin contract: The plugin still sends thumbnails and import results back to the app via the existing mechanism.
- Windows-first target

---

## 4. INFORMATION SOURCE PRIORITY

Ranked by reliability for tagging. Higher = consult first.

| Rank | Source | Available In | Notes |
|------|--------|-------------|-------|
| 1 | `pathname` files inside `.unitypackage` | `.unitypackage` | Original Unity project paths. Even a blank-named package exposes folder structure and creator name. |
| 2 | Prefab/Material YAML inside `.unitypackage` | `.unitypackage` | Blendshape names on SkinnedMeshRenderer, shader GUID refs in materials, VRCAvatarDescriptor component data. |
| 3 | `.meta` YAML inside `.unitypackage` | `.unitypackage` | Importer class (ModelImporter = mesh, TextureImporter = texture), `labels` array if creator set them. |
| 4 | ZIP central directory (file listing) | `.zip` | Full file list with zero extraction cost. Reveals structure, nested `.unitypackage` names, image candidates. |
| 5 | Nested `.unitypackage` names inside a `.zip` | `.zip` | Product name often survives even when the outer zip is generic. |
| 6 | README / txt files (first 800 chars only) | both | Avatar compatibility lists, version notes. Cap read length. |
| 7 | Image filenames and path depth | both | Candidate scoring for thumbnail selection. |
| 8 | Package filename tokens | both | Last resort. Tokenize, normalize, match against ontology. |

**OCR: explicitly excluded.** 7–15% yield, 200–500ms per image, poor accuracy on stylized/Japanese game-art fonts. Hook exists in schema for future opt-in only.

---

## 5. PIPELINE ARCHITECTURE

Four phases per package. Each phase writes to DB and moves the package to the next scan state. The UI populates progressively — a card appears after Phase 2, gets tags after Phase 3, gets a thumbnail after Phase 4.

```
PHASE 1: TRIAGE          (main thread, on drop, < 10ms)
PHASE 2: INDEX SCAN      (worker pool, streaming, no disk writes)
PHASE 3: DEEP PARSE      (worker pool, YAML parse, image score)
PHASE 4: THUMBNAIL       (separate pool, extract + resize only)
```

### Phase 1 — Triage
- Detect format by magic bytes (not extension)
- Record: absolute path, filename, file size, mtime
- Compute fast pre-hash: `size + mtime` string as cache key
- Full SHA-256 only on cache key collision (dedup detection, not storage addressing)
- Insert record into DB with state `PENDING`
- No archive reading

### Phase 2 — Index Scan
Streaming only. Nothing written to disk.

**For `.unitypackage` (gzip'd TAR):**
- Stream entries; for each GUID folder collect:
  - `pathname` → append to internal path manifest
  - `asset.meta` → parse YAML header, record importer class and labels array
  - `preview.png` → record existence and byte offset as thumbnail candidate with score boost
  - `.prefab` / `.mat` / `.controller` → queue for Phase 3 deep parse (record byte offsets, do not read yet)
- Build aggregate: set of all importer types, set of all labels, file count by type
- Emit: `has_prefab`, `has_fbx`, `has_shader`, `has_animation`, `has_psd`, `has_texture_set`, `has_unity_preview`

**For `.zip`:**
- Read central directory only (zero decompression)
- From file listing: identify structure signals, image candidates by extension and path depth
- Identify nested `.unitypackage` files → queue as child scans with parent relationship
- Identify README/txt files → queue for Phase 3 text read
- Move package to state `INDEXED`; card becomes visible in UI

### Phase 3 — Deep Parse
Runs concurrently with other packages' Phase 2. Lower priority.

**Unity YAML parse (highest value task in the whole pipeline):**
- For each queued `.prefab`: parse YAML, extract:
  - `SkinnedMeshRenderer.blendShapes` → blendshape name list (avatar compat signal)
  - `VRCAvatarDescriptor` component presence → confirms avatar base type
  - `Animator` controller references → confirms animation layer presence
- For each queued `.mat`: parse YAML, extract:
  - `m_Shader` GUID → cross-reference known shader GUID table (lilToon, Poiyomi, Standard, UTS)
  - `m_SavedProperties` texture slot names → confirms texture pack structure
- README/txt: read first 800 chars, pass through keyword extractor

**Run all Analyzers in parallel, collect Evidence objects:**

```
Evidence {
  tag: str          # canonical tag slug
  confidence: float # 0.0 – 1.0
  source: str       # which analyzer produced this
}
```

Aggregate: for each canonical tag, sum weighted confidence across all Evidence. Emit tags above threshold (default 0.5). Store full evidence list in DB.
- Move package to state `ANALYZED`; card gains tag chips

### Phase 4 — Thumbnail
Separate 2-thread pool. I/O bound.

- Run thumbnail candidate scoring (see Section 7)
- Extract only the winning candidate's bytes from the archive
- Resize to 256×256, encode as WebP, write to `thumbcache/` by archive hash
- If multiple strong candidates exist: generate a 2×2 contact sheet composite as secondary preview
- Move package to state `COMPLETE`; card thumbnail populates

---

## 6. ANALYZER MODULES

Each is a self-contained class implementing `analyze(extraction_result) -> list[Evidence]`. They run concurrently in Phase 3. Adding a new analyzer requires no changes to orchestration.

| Analyzer | Primary Signal | Key Tags Produced |
|----------|---------------|-------------------|
| `PathnameAnalyzer` | Internal Unity project paths | creator name, product name, avatar compat, content type |
| `BlendshapeAnalyzer` | SkinnedMeshRenderer blendshape names | avatar compat (highest confidence source) |
| `ShaderAnalyzer` | Material GUID → known shader table | `liltoon`, `poiyomi`, `quest_compatible`, `standard` |
| `ComponentAnalyzer` | Prefab component type list | `avatar_base` (VRCAvatarDescriptor), `physbone`, `has_animator` |
| `ImporterAnalyzer` | `.meta` importer class names | `has_fbx`, `has_texture_set`, `has_audio`, `has_script` |
| `StructureAnalyzer` | File counts and type ratios | `material_separate`, `multi_package`, `texture_only`, `no_prefab` |
| `ReadmeAnalyzer` | First 800 chars of README | avatar compat, version, install notes present |
| `ImageAnalyzer` | Image filenames and path depth | thumbnail candidate scores only, no tags |
| `FilenameAnalyzer` | Package and directory name tokens | all tags (lowest confidence, last resort) |

---

## 7. THUMBNAIL CANDIDATE SCORING

Every image found in an archive receives a float score. The highest-scoring image is extracted. The top 4 are stored as candidates for the contact sheet and for manual override in the UI.

**Positive signals:**

| Signal | Score |
|--------|-------|
| Unity-generated `preview.png` in a GUID folder | +0.90 |
| Only image in the entire archive | +0.50 |
| Filename contains: `preview`, `thumbnail`, `banner`, `promo`, `main`, `cover` | +0.45 |
| Path depth ≤ 2 from archive root | +0.30 |
| Image is landscape and width > 400px | +0.20 |
| Image has high pixel entropy (detail, not solid color) | +0.15 |

**Negative signals:**

| Signal | Score |
|--------|-------|
| Path contains `/Textures/` or `/tex/` | −0.50 |
| Filename contains: `_N`, `_normal`, `_roughness`, `_mask`, `_alpha`, `_emission`, `_metallic` | −0.60 |
| Square image smaller than 128px | −0.40 |
| Path depth > 4 | −0.15 |

**Contact sheet:** if the top 4 candidates all score above 0.3, generate a 2×2 composite at 512×512. Store as secondary preview. Show in detail panel.

**Face detection (optional, background only):** if pillow-based Haar or similar is available, run on the top-2 candidates only after the card is already COMPLETE. Upgrades the score; never blocks the pipeline.

---

## 8. TAG ONTOLOGY

Tags are not raw strings. Every tag has a canonical slug. Aliases (including Japanese) normalize into it.

### Schema

```
TagDefinition {
  id:             int
  slug:           str   # canonical, e.g. "hair"
  display_label:  str   # shown in UI, e.g. "Hair"
  category:       str   # "type" | "avatar_compat" | "structure" | "shader" | "creator"
  aliases:        list[str]   # raw signals that map here
  icon:           str   # optional icon name
}
```

### Alias Examples

```
"hair" aliases: ["hair", "hairstyle", "bang", "fronthair", "髪", "ヘア", "ヘアー"]
"manuka" aliases: ["manuka", "マヌカ", "MNK", "mnk"]
"liltoon" aliases: ["liltoon", "lilToon", "lil_toon", "lilToonShader"]
"poiyomi" aliases: ["poiyomi", "poiyomishader", "poi", "PoiyomiToonShader"]
```

The alias table lives in the DB and is user-editable. The application ships with a starter set covering the major VRC avatar bases and common content types.

### Avatar Compat Tag Seeding

Known VRC avatar bases to seed on first run (expandable by user):
Manuka, Lime, Mochi, Kikyo, Liltachi, Rue, Karin, Lunya, Popsticky, Rindo, Selestia, Shinra, Nia, Chise, Hana, Pulse, and their known Japanese/abbreviated aliases.

---

## 9. DATABASE SCHEMA (DELTA FROM CURRENT)

Additions only. Existing tables are extended, not replaced.

### Modified: `packages` table
Add columns:
```
scan_state          TEXT    -- PENDING | INDEXED | ANALYZED | COMPLETE | ERROR
content_hash        TEXT    -- SHA-256, nullable until computed
scan_error          TEXT    -- last error message if state = ERROR
has_unity_preview   INT     -- 1 if preview.png found inside unitypackage
linked_group_id     INT     -- FK to package_groups
```

### New: `evidence` table
```
id              INT PRIMARY KEY
package_id      INT     FK → packages.id
tag_slug        TEXT    FK → tag_definitions.slug
confidence      REAL    -- 0.0 to 1.0
source          TEXT    -- which analyzer produced this
raw_signal      TEXT    -- the actual string that fired (for debugging)
```

### New: `tag_definitions` table
```
id              INT PRIMARY KEY
slug            TEXT UNIQUE
display_label   TEXT
category        TEXT    -- type | avatar_compat | structure | shader | creator
icon            TEXT    -- nullable
```

### New: `tag_aliases` table
```
id              INT PRIMARY KEY
tag_slug        TEXT    FK → tag_definitions.slug
alias           TEXT    -- raw signal string, lowercased
```

### New: `pathname_index` table
All internal Unity paths from all packages. Enables deep search without re-scanning.
```
id              INT PRIMARY KEY
package_id      INT     FK → packages.id
path            TEXT
importer_class  TEXT    -- nullable
```

### New: `thumbnail_candidates` table
```
id              INT PRIMARY KEY
package_id      INT     FK → packages.id
archive_path    TEXT    -- path inside archive
score           REAL
extracted       INT     -- 0 | 1
cache_path      TEXT    -- path in thumbcache/, nullable until extracted
is_contact_sheet INT    -- 1 if this is the composite
ocr_candidate   INT     -- 1 if flagged for future OCR (never auto-run)
```

### New: `package_groups` table
For linked packages (e.g., outfit + separate materials pack).
```
id              INT PRIMARY KEY
display_name    TEXT    -- inferred or user-set
link_confidence REAL    -- how confident the auto-link is
link_source     TEXT    -- "pathname_root" | "filename_similarity" | "user"
```

### New: `shader_guids` table
Known shader GUIDs. Ships with a starter set, user-extensible.
```
guid            TEXT PRIMARY KEY
shader_name     TEXT    -- e.g. "lilToon"
tag_slug        TEXT    -- e.g. "liltoon"
quest_compatible INT    -- 1 | 0 | NULL (unknown)
```

### FTS5 Index
```sql
CREATE VIRTUAL TABLE search_index USING fts5(
  package_id UNINDEXED,
  content,          -- filename + display name
  paths,            -- space-joined pathname_index entries
  readme_excerpt,   -- first 800 chars of readme
  tags              -- space-joined canonical tag slugs
);
```

---

## 10. WORKER ARCHITECTURE

```
Main Thread         UI only. No file I/O. No archive reads.

ScanWorkerPool      4 threads. Phase 1 + Phase 2. One package per thread.
                    Emits: package_indexed signal → UI shows card.

AnalyzerPool        4 threads. Phase 3. Runs concurrently with scan pool.
                    Emits: package_analyzed signal → UI updates tags.

ThumbnailPool       2 threads. Phase 4. Lower priority than scan/analyze.
                    Emits: package_thumbnailed signal → UI updates card image.

DBWriteThread       1 thread. Single writer. All pools push to a queue.
                    SQLite in WAL mode. Readers never blocked.
```

Signals use PySide6's `Signal` mechanism across threads. Workers never touch UI objects directly.

---

## 11. LINKED PACKAGE DETECTION

Runs as a post-scan pass after each batch of packages is fully indexed.

**Detection method 1 — pathname root overlap (high confidence):**
If two packages both contain `pathname` entries starting with `Assets/[CreatorX]/ProductY/`, they are linked. Confidence 0.95.

**Detection method 2 — filename similarity (medium confidence):**
Tokenize filenames, strip version tokens (`v1`, `v2`, `update`, `fix`). If normalized token sets overlap > 70%, flag as candidate link. Confidence 0.65. Requires user confirmation before grouping.

**UI treatment:**
Linked packages share one expandable card. Combined tag set shown. "Open With Unity" sends the full group.

---

## 12. UI CHANGES

### Filter Panel
- Tag chips grouped by category: Type / Avatar Compat / Shader / Structure / Creator
- Per-group AND/OR toggle
- Confidence threshold slider: hide tags below N% (default 50%)
- Text search spans: display name, internal pathnames (via FTS5), readme excerpt, tag slugs
- Sort: date added, display name, type, completeness (scan state)
- Quick filters: `Unreviewed`, `No thumbnail`, `Linked packages`, `No prefab found`

### Asset Card
- Thumbnail 256×256; fallback = category icon
- Display name (editable inline, double-click)
- Top 5 tag chips; `+N more` overflow badge
- Confidence indicator: full color ≥ 0.7, muted 0.4–0.69, italic < 0.4
- Scan state indicator: spinner (PENDING/INDEXED), checkmark (COMPLETE), warning (ERROR)
- Linked badge if part of a group; click to expand group

### Detail Panel
- All thumbnail candidates shown as a strip — click to promote to primary
- Contact sheet shown if generated
- Full tag list with confidence bars and source label (e.g., "blendshape", "pathname", "filename")
- Add / remove / override tags with autocomplete from `tag_definitions`
- Internal pathname tree (the full manifest — most useful for understanding minimal packages)
- Metadata: creator, archive format, file count by type, total size, scan state, content hash
- Notes field (freetext, stored in DB)
- Linked packages section: shows sibling packages with their own tag summary
- "Open With Unity" — sends full linked group if applicable

### Bulk Edit
- Multi-select → shared tag edit panel
- "Link as group" action on selection
- "Set thumbnail from file" (file picker, overrides candidate system)

---

## 13. INCREMENTAL RESCAN (STARTUP BEHAVIOR)

On launch, compare every tracked package's `size + mtime` against stored values.

- Match: skip all phases, package is COMPLETE as-is
- Mismatch: reset to PENDING, queue for full rescan
- Missing from disk: mark as ORPHANED, show with warning indicator

A library of 500 packages should complete this check in under 2 seconds on spinning disk.

---

## 14. DEEP ANALYSIS MODE (UNITY INTEGRATION — DEFERRED)

Never runs automatically. Accessed via right-click → Deep Analyze.

This mode:
- Triggers headless Unity import via the existing plugin
- Unity generates a proper rendered prefab thumbnail and sends it back
- Unity import log is recorded (missing dependencies surface the `material_separate` relationship)
- Rendered thumbnail is promoted as highest-confidence candidate in `thumbnail_candidates`

This is a separate development phase. The schema and plugin contract are designed to accommodate it without structural changes.

---

## 15. MIGRATION FROM CURRENT VERSION

On first launch after rewrite:

1. Read all existing package records from current DB
2. Treat every existing package as a fresh `PENDING` entry
3. Run the full 4-phase pipeline on each
4. Any tags previously set by the user are preserved as `Evidence` with source `"user"` and confidence `1.0` (user tags are never overwritten by analyzers, only supplemented)
5. Existing thumbnail cache entries are checked: if the file exists and scores above threshold under the new system, it is kept; otherwise re-extracted

User notes, manual tag edits, and "Open With" history are fully preserved.

---

## 16. FILES AND MODULES TO REPLACE

| Current file/module | Replacement | Reason |
|---|---|---|
| Filename dictionary scan | `PathnameAnalyzer` + `FilenameAnalyzer` (as fallback) | Dictionary is fragile; pathnames are authoritative |
| Thumbnail heuristic | `ImageAnalyzer` + `ThumbnailPool` with scoring | Replaces hope-based selection with scored candidates |
| Synchronous archive scan | 4-phase async pipeline with worker pools | Unblocks UI |
| Flat tag strings in DB | `evidence` + `tag_definitions` + `tag_aliases` tables | Enables confidence, normalization, source audit |
| (none) | `BlendshapeAnalyzer` | New; highest-value source for avatar compat |
| (none) | `ShaderAnalyzer` + `shader_guids` table | New; shader detection from material GUIDs |
| (none) | `pathname_index` table | New; enables deep search and linked-package detection |
| (none) | Contact sheet generator | New; improves browsing comprehension of multi-outfit packs |

---

## 17. NON-GOALS FOR THIS REWRITE

These are explicitly out of scope to keep the rewrite bounded:

- 3D mesh preview / viewport
- Online marketplace integration or price tracking
- Cloud sync or multi-machine library sharing
- OCR (hook exists in schema; never auto-runs)
- AI image classification (same reasoning as OCR)
- Automatic VCC / ALCOM integration (separate future phase)
- Relocating or managing the user's actual files

---

## 18. SUCCESS CRITERIA

The rewrite is complete when:

- A package named `upload_v3.unitypackage` containing a single prefab with Manuka blendshape names is automatically tagged `avatar_compat:manuka` with confidence ≥ 0.85
- A `.zip` containing two linked packages (prefabs + separate materials) is detected and grouped automatically
- A library of 200 packages scans to COMPLETE state in under 60 seconds on a mid-range machine (SSD assumed)
- Startup rescan of the same 200 unchanged packages completes in under 3 seconds
- All existing user data (notes, manual tags, thumbnail overrides) survives the migration pass
- The Unity plugin thumbnail promotion pathway functions without changes to the plugin itself
