from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from PIL import Image

from vrc_organizer.database.queries import Queries
from vrc_organizer.scanner.booth_zip import _thumb_score
from vrc_organizer.workers.base import BaseWorker

THUMB_SIZE = (256, 256)
MAX_THUMB_SOURCE = 16 * 1024 * 1024


class ThumbWorker(BaseWorker):
    """Background worker that hunts for thumbnails in pending assets."""

    def __init__(self, queries: Queries, thumb_cache_dir: Path, limit: int = 50):
        super().__init__()
        self._queries = queries
        self._thumb_cache_dir = thumb_cache_dir
        self._limit = limit

    def _run(self) -> list[int]:
        found_ids: list[int] = []
        pending = self._queries.get_pending_thumbs(limit=self._limit)

        for asset in pending:
            if self._is_cancelled:
                break

            self.signals.status.emit(f"Thumbnail: {asset.filename}")

            # Check if user labeled a cover image for this asset.
            # '__cached__' is a sentinel from the labeler meaning "the
            # asset has no archive images; keep the existing cached thumb",
            # so we leave it alone (just promote state back to 'ready').
            cover_label = self._queries.get_cover_label(asset.id)
            if cover_label == "__cached__":
                if asset.thumbnail and Path(asset.thumbnail).exists():
                    self._queries.update_thumbnail(asset.id, Path(asset.thumbnail), "ready")
                    continue
                data = self._hunt_thumb(asset.filepath, asset.filetype)
            elif cover_label:
                data = self._extract_labeled_cover(asset.filepath, asset.filetype, cover_label)
                # Labeled entry not retrievable (file moved, archive changed,
                # path mismatch) — fall back to the auto hunt instead of
                # leaving the asset thumbnail-less.
                if not data:
                    data = self._hunt_thumb(asset.filepath, asset.filetype)
            else:
                data = self._hunt_thumb(asset.filepath, asset.filetype)

            if data:
                try:
                    thumb_path = self._thumb_cache_dir / f"{asset.id}.png"
                    thumb_path.parent.mkdir(parents=True, exist_ok=True)
                    thumb_path.write_bytes(data)
                    self._queries.update_thumbnail(asset.id, thumb_path, "ready")
                    found_ids.append(asset.id)
                    self.signals.file_done.emit(asset.filename)
                except Exception:
                    self._queries.update_thumbnail(asset.id, None, "error")
            else:
                # Preserve any existing thumbnail path so the previous
                # image keeps rendering instead of going blank.
                self._queries.update_thumbnail(asset.id, asset.thumbnail, "error")

        return found_ids

    def _extract_labeled_cover(self, filepath: Path, filetype: str, entry_name: str) -> bytes | None:
        """Extract a specific image from an archive based on the user's cover label."""
        if not filepath.exists():
            return None
        basename = entry_name.rsplit("/", 1)[-1] if "/" in entry_name else entry_name
        try:
            if filetype in ("booth_zip",):
                suffix = filepath.suffix.lower()
                if suffix == ".rar":
                    import rarfile as rf
                    with rf.RarFile(filepath) as rar:
                        try:
                            raw = rar.read(entry_name)
                        except Exception:
                            raw = None
                            for info in rar.infolist():
                                if info.filename.endswith("/" + basename) or info.filename == basename:
                                    raw = rar.read(info)
                                    break
                        if raw:
                            return self._render_thumb(raw)
                else:
                    with zipfile.ZipFile(filepath, "r") as zf:
                        try:
                            raw = zf.read(entry_name)
                        except KeyError:
                            raw = None
                            for info in zf.infolist():
                                if info.filename.endswith("/" + basename) or info.filename == basename:
                                    raw = zf.read(info)
                                    break
                        if raw:
                            return self._render_thumb(raw)
            elif filetype == "unitypackage":
                with tarfile.open(filepath, "r:gz") as tf:
                    for member in tf.getmembers():
                        if member.isfile() and member.name.endswith("/" + basename):
                            f = tf.extractfile(member)
                            if f:
                                return self._render_thumb(f.read())
        except Exception:
            pass
        return None

    def _render_thumb(self, raw: bytes) -> bytes | None:
        """Convert raw image bytes to a thumbnail PNG."""
        try:
            img = Image.open(io.BytesIO(raw))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None

    def _hunt_thumb(self, filepath: Path, filetype: str) -> bytes | None:
        """Try harder to find a thumbnail for this asset."""
        if not filepath.exists():
            return None

        if filetype == "booth_zip":
            suffix = filepath.suffix.lower()
            if suffix == ".rar":
                return self._hunt_in_rar(filepath)
            return self._hunt_in_zip(filepath)
        if filetype == "unitypackage":
            return self._hunt_in_unitypackage(filepath)
        if filetype == "image":
            return self._hunt_image(filepath)
        return None

    def _hunt_in_zip(self, filepath: Path) -> bytes | None:
        """Recursively scan zip for images, including nested .unitypackage and .zip files."""
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                return self._hunt_zip_recursive(zf, max_depth=4)
        except Exception:
            return None

    def _hunt_zip_recursive(self, zf: zipfile.ZipFile, max_depth: int) -> bytes | None:
        best_score = 0
        best_data: bytes | None = None

        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            size = info.file_size

            # Direct images
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".psd")):
                if size < MAX_THUMB_SOURCE:
                    score = _thumb_score(name, size)
                    if score > best_score:
                        best_score = score
                        try:
                            raw = zf.read(info)
                            img = Image.open(io.BytesIO(raw))
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGBA")
                            else:
                                img = img.convert("RGB")
                            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            best_data = buf.getvalue()
                        except Exception:
                            pass

            # Recurse into nested archives
            if max_depth > 1:
                low = name.lower()
                if low.endswith(".unitypackage"):
                    try:
                        up_data = zf.read(info)
                        result = self._hunt_in_unitypackage_data(up_data)
                        if result:
                            return result
                    except Exception:
                        pass
                elif low.endswith(".zip") and size < 200 * 1024 * 1024:
                    try:
                        inner_data = zf.read(info)
                        with zipfile.ZipFile(io.BytesIO(inner_data), "r") as inner:
                            result = self._hunt_zip_recursive(inner, max_depth - 1)
                            if result:
                                return result
                    except Exception:
                        pass

        return best_data

    def _hunt_in_unitypackage(self, filepath: Path) -> bytes | None:
        """Open a .unitypackage and hunt for images."""
        try:
            raw = filepath.read_bytes()
            return self._hunt_in_unitypackage_data(raw)
        except Exception:
            return None

    def _hunt_in_unitypackage_data(self, data: bytes) -> bytes | None:
        """Hunt for thumbnail images inside a .unitypackage tar.gz in memory."""
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        except Exception:
            return None

        best_score = 0
        best_data: bytes | None = None
        try:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = member.name
                size = member.size
                if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".psd")):
                    continue
                if size > MAX_THUMB_SOURCE:
                    continue
                score = _thumb_score(name, size)
                if score > best_score:
                    best_score = score
                    try:
                        f = tf.extractfile(member)
                        if f:
                            raw = f.read()
                            img = Image.open(io.BytesIO(raw))
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGBA")
                            else:
                                img = img.convert("RGB")
                            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            best_data = buf.getvalue()
                    except Exception:
                        pass
        finally:
            tf.close()
        return best_data

    def _hunt_in_rar(self, filepath: Path) -> bytes | None:
        """Scan a RAR file for thumbnail images."""
        import rarfile as rf
        try:
            with rf.RarFile(filepath) as rar:
                best_score = 0
                best_data: bytes | None = None
                for info in rar.infolist():
                    if info.isdir():
                        continue
                    name = info.filename
                    size = info.file_size
                    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".psd")):
                        continue
                    if size > MAX_THUMB_SOURCE:
                        continue
                    score = _thumb_score(name, size)
                    if score > best_score:
                        best_score = score
                        try:
                            raw = rar.read(name)
                            img = Image.open(io.BytesIO(raw))
                            if img.mode in ("RGBA", "P"):
                                img = img.convert("RGBA")
                            else:
                                img = img.convert("RGB")
                            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
                            buf = io.BytesIO()
                            img.save(buf, format="PNG")
                            best_data = buf.getvalue()
                        except Exception:
                            pass
                return best_data
        except Exception:
            return None

    def _hunt_image(self, filepath: Path) -> bytes | None:
        """Re-render an image file as thumbnail."""
        try:
            img = Image.open(filepath)
            img = img.copy()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGBA")
            else:
                img = img.convert("RGB")
            img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return None
