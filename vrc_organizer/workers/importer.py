from __future__ import annotations

import os
import zipfile
import shutil
import logging
from pathlib import Path
from typing import Optional

from vrc_organizer.auto_tagger import suggest_tags, suggest_genre
from vrc_organizer.database.queries import Queries
from vrc_organizer.scanner.orchestrator import scan_file
from vrc_organizer.workers.base import BaseWorker

logger = logging.getLogger(__name__)


class ImportWorker(BaseWorker):
    def __init__(self, file_paths: list[str], queries: Queries,
                 thumb_cache_dir: Path, library_dir: Optional[Path] = None):
        super().__init__()
        self._paths = file_paths
        self._queries = queries
        self._thumb_cache_dir = thumb_cache_dir
        self._library_dir = library_dir

    def _run(self) -> list[int]:
        imported_ids: list[int] = []
        total = len(self._paths)

        for i, path_str in enumerate(self._paths):
            if self._is_cancelled:
                break

            filepath = Path(path_str)
            if not filepath.exists():
                self.signals.progress.emit(int((i + 1) / total * 100))
                continue

            self.signals.status.emit(f"Processing {filepath.name} ({i + 1} of {total})...")

            try:
                asset_id = self._import_one(filepath)
                if asset_id:
                    imported_ids.append(asset_id)
                    self.signals.file_done.emit(filepath.name)
            except Exception as e:
                logger.warning("Import failed for %s: %s", filepath, e)
                self.signals.file_failed.emit(filepath.name, str(e))

            self.signals.progress.emit(int((i + 1) / total * 100))

        return imported_ids

    def _import_one(self, filepath: Path) -> int | None:
        """Import a single file. Returns asset_id or None."""
        if self._is_cancelled:
            return None

        # Skip if unchanged
        try:
            stat = filepath.stat()
        except OSError:
            return None
        file_size = stat.st_size
        mod_time = stat.st_mtime

        existing = self._queries.get_asset_by_path(filepath)
        if existing is not None and existing.mod_time == mod_time:
            return existing.id

        # Extract archives before scanning
        if self._is_cancelled:
            return None
        extracted_path = self._maybe_extract(filepath)

        if self._is_cancelled:
            return None
        report = scan_file(filepath)

        if self._is_cancelled:
            return None
        asset_id = self._queries.insert_asset(
            filename=filepath.name,
            filepath=extracted_path or filepath,
            filetype=report.filetype,
            file_size=file_size,
            mod_time=mod_time,
        )

        if report.contents:
            self._queries.insert_scan_results(asset_id, report.contents)

        if report.thumbnail_source:
            try:
                thumb_path = self._save_thumbnail(asset_id, report.thumbnail_source)
                self._queries.update_thumbnail(asset_id, thumb_path, "ready")
            except Exception as e:
                logger.warning("Thumbnail save failed for asset %d: %s", asset_id, e)
                self._queries.update_thumbnail(asset_id, None, "error")
        else:
            self._queries.update_thumbnail(asset_id, None, "pending")

        self._queries.update_scan_state(asset_id, "done")

        # Auto-tag based on filename + extracted folder names.
        # Note: build the id↔name map AFTER suggest_tags runs — it may have
        # created new tags, and the genre selector must see those names to
        # classify correctly. The previous version snapshotted before
        # suggest_tags, which silently dropped fresh tag IDs from the input
        # to suggest_genre and produced wrong genres for assets that
        # introduced new tags.
        try:
            from vrc_organizer.tag_data import GENRE_NAMES

            tag_ids = suggest_tags(self._queries, filepath.name, extracted_path)

            all_tags = self._queries.get_all_tags()
            id_to_name = {tid: name for tid, name, _, _ in all_tags}
            name_to_id = {name: tid for tid, name, _, _ in all_tags}

            suggested_names = {id_to_name[tid] for tid in tag_ids if tid in id_to_name}
            genre_name = suggest_genre(filepath.name, report.filetype, suggested_names)
            genre_id = name_to_id.get(genre_name)
            if not genre_id:
                genre_id = self._queries.create_tag(genre_name, "#6366f1")

            # Build the final tag set: all non-genre suggestions plus the
            # chosen genre. Insert each once — no add-then-remove churn, and
            # the asset never briefly carries multiple genre tags.
            final_ids: set[int] = {
                tid for tid in tag_ids
                if id_to_name.get(tid) not in GENRE_NAMES
            }
            if genre_id:
                final_ids.add(genre_id)

            for tag_id in final_ids:
                self._queries.add_tag_to_asset(asset_id, tag_id)

            if len(final_ids) >= 2:
                self._queries.record_tag_cooccurrence(list(final_ids))
        except Exception:
            logger.warning("Auto-tagging failed for %s", filepath.name, exc_info=True)

        return asset_id

    def _maybe_extract(self, filepath: Path) -> Optional[Path]:
        """Extract zip/rar archives to library directory. Returns extraction path."""
        if not self._library_dir:
            self._library_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "VrcAssetOrganizer" / "Library"

        suffix = filepath.suffix.lower()
        pack_name = filepath.stem
        extract_to = self._library_dir / pack_name

        if suffix == '.zip' and not extract_to.exists():
            try:
                extract_to.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(filepath, 'r') as zf:
                    zf.extractall(extract_to)
                _extract_nested_zips(extract_to, max_depth=3)
                return extract_to
            except Exception as e:
                logger.warning("Failed to extract zip %s: %s", filepath, e)
                shutil.rmtree(extract_to, ignore_errors=True)
                return None

        if suffix == '.rar':
            try:
                import subprocess
                extract_to.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    ['unrar', 'x', '-o+', str(filepath), str(extract_to)],
                    capture_output=True, timeout=300
                )
                if result.returncode == 0:
                    return extract_to
                logger.warning("unrar failed for %s: %s", filepath,
                               result.stderr.decode(errors='replace').strip())
            except FileNotFoundError:
                logger.warning("unrar not installed — cannot extract %s", filepath)
            except Exception as e:
                logger.warning("RAR extraction error for %s: %s", filepath, e)

        return None

    def _save_thumbnail(self, asset_id: int, data: bytes) -> Path:
        thumb_path = self._thumb_cache_dir / f"{asset_id}.png"
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(data)
        return thumb_path


def _extract_nested_zips(root: Path, max_depth: int):
    """Recursively extract nested .zip files found within an extracted archive."""
    if max_depth <= 0:
        return
    try:
        for child in root.iterdir():
            if child.is_dir():
                _extract_nested_zips(child, max_depth - 1)
            elif child.suffix.lower() == '.zip':
                dest = root / child.stem
                if not dest.exists():
                    try:
                        dest.mkdir(parents=True, exist_ok=True)
                        with zipfile.ZipFile(child, 'r') as inner:
                            inner.extractall(dest)
                        _extract_nested_zips(dest, max_depth - 1)
                    except Exception as e:
                        logger.warning("Failed to extract nested zip %s: %s", child, e)
                        shutil.rmtree(dest, ignore_errors=True)
    except OSError:
        pass
