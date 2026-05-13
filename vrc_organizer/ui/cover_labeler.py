"""Cover Labeler — captcha-style UI for selecting the best thumbnail image."""
from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QFont, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QWidget, QProgressBar, QFrame, QMessageBox,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset

CARD_SIZE = 160
MAX_IMAGES = 6


class _ImageCard(QFrame):
    """Clickable image card with keyboard shortcut indicator."""
    clicked = Signal(str)

    def __init__(self, index: int, entry_name: str, pixmap: QPixmap | None,
                 width: int = 0, height: int = 0, parent=None):
        super().__init__(parent)
        self._entry_name = entry_name
        self._index = index
        self._width = width
        self._height = height

        self.setFixedSize(CARD_SIZE + 16, CARD_SIZE + 40)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QFrame {
                background: white;
                border: 2px solid #e2e8f0;
                border-radius: 12px;
            }
            QFrame:hover {
                border-color: #3b82f6;
                background: #f8fafc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(4)

        # Image container with badge
        img_container = QWidget()
        img_container.setFixedSize(CARD_SIZE, CARD_SIZE)

        img_label = QLabel(img_container)
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setFixedSize(CARD_SIZE, CARD_SIZE)
        img_label.setStyleSheet("background: transparent;")
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(CARD_SIZE, CARD_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            img_label.setPixmap(scaled)
        else:
            img_label.setText("?")
            img_label.setStyleSheet("background: #f1f5f9; border-radius: 8px; font-size: 28px; color: #94a3b8;")

        # Shortcut badge
        badge = QLabel(str(index + 1), img_container)
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background: #3b82f6; color: white; font-weight: bold; "
            "border-radius: 14px; font-size: 13px;"
        )
        badge.move(4, 4)

        layout.addWidget(img_container)

        # Dimensions label
        if width and height:
            dim_label = QLabel(f"{width} × {height}")
            dim_label.setAlignment(Qt.AlignCenter)
            dim_label.setStyleSheet("color: #94a3b8; font-size: 11px;")
            layout.addWidget(dim_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._entry_name)
        super().mousePressEvent(event)

    @property
    def dimensions(self) -> tuple[int, int]:
        return self._width, self._height


class CoverLabelerDialog(QDialog):
    """Captcha-style dialog for labeling the best cover image per asset."""
    labeling_complete = Signal()

    def __init__(self, queries: Queries, thumb_cache_dir: Path = None, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._thumb_cache_dir = thumb_cache_dir
        self._assets: list[Asset] = []
        self._current_idx = 0
        self._cards: list[_ImageCard] = []
        self._image_data: dict[str, tuple[bytes, int, int, int]] = {}
        self._loading_more = False

        self.setWindowTitle("Label Covers")
        self.resize(600, 480)
        self._setup_ui()
        self._load_assets()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 16, 20, 16)

        # Header row
        header = QHBoxLayout()
        self._title = QLabel("Loading...")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        self._title.setFont(font)
        header.addWidget(self._title)
        header.addStretch()

        self._progress = QProgressBar()
        self._progress.setFixedWidth(120)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar { background: #e2e8f0; border-radius: 4px; height: 8px; }
            QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
        """)
        header.addWidget(self._progress)
        layout.addLayout(header)

        # Filename
        self._filename = QLabel("")
        self._filename.setWordWrap(True)
        self._filename.setStyleSheet("color: #64748b;")
        layout.addWidget(self._filename)

        # Image grid
        self._grid_widget = QWidget()
        self._grid_widget.setStyleSheet("background: #f8fafc; border-radius: 8px;")
        self._grid = QGridLayout(self._grid_widget)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._grid_widget, 1)

        # Footer
        footer = QHBoxLayout()
        self._count = QLabel("")
        self._count.setStyleSheet("color: #94a3b8; font-size: 12px;")
        footer.addWidget(self._count)
        footer.addStretch()

        skip_btn = QPushButton("Skip")
        skip_btn.setFixedWidth(70)
        skip_btn.setStyleSheet("""
            QPushButton { background: #f1f5f9; color: #64748b; border: none;
                          border-radius: 6px; padding: 8px; font-weight: 500; }
            QPushButton:hover { background: #e2e8f0; }
        """)
        skip_btn.clicked.connect(self._on_skip)
        footer.addWidget(skip_btn)

        done_btn = QPushButton("Done")
        done_btn.setFixedWidth(70)
        done_btn.setStyleSheet("""
            QPushButton { background: #3b82f6; color: white; border: none;
                          border-radius: 6px; padding: 8px; font-weight: 500; }
            QPushButton:hover { background: #2563eb; }
        """)
        done_btn.clicked.connect(self._on_done)
        footer.addWidget(done_btn)
        layout.addLayout(footer)

    def _load_assets(self):
        self._assets = self._queries.get_unlabeled_cover_assets(limit=200)
        if not self._assets:
            self._title.setText("All Done")
            self._filename.setText("No more assets need cover labels.")
            self._progress.setVisible(False)
            return
        self._current_idx = 0
        self._progress.setRange(0, len(self._assets))
        self._show_current()

    def _prefetch_more(self):
        """Load another batch of assets and append to the queue."""
        if self._loading_more:
            return
        self._loading_more = True
        try:
            more = self._queries.get_unlabeled_cover_assets(limit=100)
            existing_ids = {a.id for a in self._assets}
            new_assets = [a for a in more if a.id not in existing_ids]
            if new_assets:
                self._assets.extend(new_assets)
                self._progress.setRange(0, len(self._assets))
        finally:
            self._loading_more = False

    FALLBACK_KEY = "__cached_thumbnail__"

    def _show_current(self):
        self._clear_grid()
        self._image_data.clear()

        if self._current_idx >= len(self._assets):
            self._finish()
            return

        asset = self._assets[self._current_idx]
        self._title.setText(f"{self._current_idx + 1} / {len(self._assets)}")
        self._progress.setValue(self._current_idx)
        self._filename.setText(f"Loading images...\n{asset.filename}")

        entries = self._get_image_entries(asset)
        loaded = self._load_images(asset, entries)
        self._filename.setText(asset.filename)

        # Fallback: if no images could be extracted from archives,
        # show the cached thumbnail as an option.
        if loaded == 0 and self._thumb_cache_dir:
            fallback_path = self._thumb_cache_dir / f"{asset.id}.png"
            if fallback_path.exists():
                pix = QPixmap(str(fallback_path))
                if not pix.isNull():
                    card = _ImageCard(0, self.FALLBACK_KEY, pix, 0, 0)
                    card.clicked.connect(self._on_card_clicked)
                    self._grid.addWidget(card, 0, 0)
                    self._cards.append(card)
                    loaded = 1

        self._count.setText(f"Press 1–{len(self._cards)} or click" if loaded > 0 else "No images could be loaded")

    def _get_image_entries(self, asset: Asset) -> list[str]:
        results = self._queries.get_scan_results(asset.id)
        images = []
        for name, etype, _ in results:
            if etype != "image":
                continue
            # Skip images inside nested archives (unitypackage/guid/path)
            if ".unitypackage/" in name.lower():
                continue
            images.append(name)
        return images

    def _load_images(self, asset: Asset, entries: list[str]) -> int:
        """Load images and return count of successfully loaded images."""
        # If the archive was extracted to a directory, find images directly on disk.
        # This is simpler and more reliable than matching scan_result entry names.
        if asset.filepath.is_dir():
            return self._load_images_from_directory(asset.filepath)

        # Otherwise read from the archive file (zip / unitypackage / rar).
        loaded_count = 0
        card_idx = 0
        for entry_name in entries:
            data, w, h, depth = self._extract_image(asset.filepath, entry_name)
            if not data:
                continue

            pix = QPixmap()
            pix.loadFromData(data)
            if pix.isNull():
                continue

            self._image_data[entry_name] = (data, w, h, depth)
            card = _ImageCard(card_idx, entry_name, pix, w, h)
            card.clicked.connect(self._on_card_clicked)
            row, col = divmod(card_idx, 3)
            self._grid.addWidget(card, row, col)
            self._cards.append(card)
            card_idx += 1
            loaded_count += 1

            if card_idx >= MAX_IMAGES:
                break

        return loaded_count

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}

    def _load_images_from_directory(self, dirpath: Path) -> int:
        """Find all image files recursively in an extracted archive directory."""
        # Collect images: score by filename, take top MAX_IMAGES
        found: list[tuple[int, Path]] = []  # (score, path)
        for file_path in dirpath.rglob("*"):
            if file_path.suffix.lower() in self.IMAGE_SUFFIXES and file_path.is_file():
                try:
                    size = file_path.stat().st_size
                    if size > 50 * 1024 * 1024:  # skip >50MB
                        continue
                    score = self._compute_filename_score(file_path.name)
                    found.append((score, file_path))
                except OSError:
                    pass

        # Sort by score descending, then by filename
        found.sort(key=lambda x: (-x[0], x[1].name))

        loaded_count = 0
        for card_idx, (score, file_path) in enumerate(found):
            if card_idx >= MAX_IMAGES:
                break

            try:
                data = file_path.read_bytes()
            except OSError:
                continue

            pix = QPixmap()
            pix.loadFromData(data)
            if pix.isNull():
                continue

            # Use path relative to dirpath as the entry name
            try:
                entry_name = file_path.relative_to(dirpath).as_posix()
            except ValueError:
                entry_name = file_path.name

            w, h = self._get_image_dimensions(data)
            depth = entry_name.count("/")
            self._image_data[entry_name] = (data, w, h, depth)
            card = _ImageCard(card_idx, entry_name, pix, w, h)
            card.clicked.connect(self._on_card_clicked)
            row, col = divmod(card_idx, 3)
            self._grid.addWidget(card, row, col)
            self._cards.append(card)
            loaded_count += 1

        return loaded_count

    def _extract_image(self, filepath: Path, entry_name: str) -> tuple[bytes | None, int, int, int]:
        suffix = filepath.suffix.lower()
        basename = entry_name.rsplit("/", 1)[-1] if "/" in entry_name else entry_name
        # Handle backslash separators (Windows paths stored in scan results)
        if "\\" in basename:
            basename = basename.rsplit("\\", 1)[-1]
        depth = entry_name.count("/") + entry_name.count("\\")

        if not filepath.exists():
            return None, 0, 0, depth

        try:
            data = None
            if filepath.is_dir():
                data = self._extract_from_directory(filepath, entry_name, basename)
            elif suffix == ".zip":
                data = self._extract_from_zip(filepath, entry_name, basename)
            elif suffix == ".unitypackage":
                data = self._extract_from_unitypackage(filepath, entry_name, basename)
            elif suffix == ".rar":
                data = self._extract_from_rar(filepath, entry_name, basename)
            elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
                try:
                    data = filepath.read_bytes()
                except Exception:
                    pass

            if data:
                w, h = self._get_image_dimensions(data)
                return data, w, h, depth
        except Exception:
            pass
        return None, 0, 0, depth

    def _extract_from_directory(self, dirpath: Path, entry_name: str, basename: str) -> bytes | None:
        """Read an image file from an already-extracted archive directory."""
        try:
            normalized = entry_name.replace("\\", "/")
            file_path = dirpath / normalized
            if file_path.is_file():
                return file_path.read_bytes()
            # Fall back to recursive basename search
            for found in dirpath.rglob(basename):
                if found.is_file():
                    return found.read_bytes()
        except Exception:
            pass
        return None

    def _extract_from_zip(self, filepath: Path, entry_name: str, basename: str) -> bytes | None:
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                # Try exact match first
                try:
                    return zf.read(entry_name)
                except KeyError:
                    pass

                # Normalize path separators and try again
                normalized = entry_name.replace("\\", "/")
                for info in zf.infolist():
                    info_norm = info.filename.replace("\\", "/")
                    if info_norm == normalized:
                        return zf.read(info)
                    if info_norm.endswith("/" + basename) or info_norm == basename:
                        return zf.read(info)

                # Try without leading folder
                if "/" in entry_name:
                    shorter = entry_name.split("/", 1)[1]
                    try:
                        return zf.read(shorter)
                    except KeyError:
                        pass
        except Exception:
            pass
        return None

    def _extract_from_unitypackage(self, filepath: Path, entry_name: str, basename: str) -> bytes | None:
        try:
            with tarfile.open(filepath, "r:gz") as tf:
                # Build GUID → pathname map
                guid_to_path: dict[str, str] = {}
                for member in tf.getmembers():
                    if member.isfile() and member.name.endswith("/pathname"):
                        guid = member.name.split("/", 1)[0]
                        f = tf.extractfile(member)
                        if f:
                            try:
                                guid_to_path[guid] = f.read().decode("utf-8", errors="replace").strip()
                            except Exception:
                                pass

                # Try exact entry_name match first (normalized)
                entry_norm = entry_name.replace("\\", "/").lower()
                target_guid = None
                for guid, path in guid_to_path.items():
                    if path.replace("\\", "/").lower() == entry_norm:
                        target_guid = guid
                        break

                # Fall back to basename match
                if not target_guid:
                    for guid, path in guid_to_path.items():
                        path_norm = path.replace("\\", "/").lower()
                        if path_norm.endswith("/" + basename.lower()) or path_norm == basename.lower():
                            target_guid = guid
                            break

                if target_guid:
                    # Read the asset file from that GUID directory
                    asset_path = f"{target_guid}/asset"
                    for member in tf.getmembers():
                        if member.name == asset_path and member.isfile():
                            f = tf.extractfile(member)
                            if f:
                                return f.read()
        except tarfile.ReadError:
            # Some unitypackages are actually zips
            try:
                with zipfile.ZipFile(filepath, "r") as zf:
                    for info in zf.infolist():
                        info_norm = info.filename.replace("\\", "/").lower()
                        if info_norm.endswith("/" + basename.lower()) or info_norm == basename.lower():
                            return zf.read(info)
            except Exception:
                pass
        except Exception:
            pass
        return None

    def _extract_from_rar(self, filepath: Path, entry_name: str, basename: str) -> bytes | None:
        try:
            import rarfile
            with rarfile.RarFile(filepath, "r") as rf:
                try:
                    return rf.read(entry_name)
                except KeyError:
                    pass
                for info in rf.infolist():
                    if info.filename.endswith("/" + basename) or info.filename == basename:
                        return rf.read(info)
        except ImportError:
            pass
        return None

    def _get_image_dimensions(self, data: bytes) -> tuple[int, int]:
        try:
            img = Image.open(io.BytesIO(data))
            return img.size
        except Exception:
            return 0, 0

    def _clear_grid(self):
        for card in self._cards:
            card.hide()
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

    def _on_card_clicked(self, entry_name: str):
        self._save_and_advance(entry_name)

    def _save_and_advance(self, entry_name: str):
        asset = self._assets[self._current_idx]

        w, h, depth, score = 0, 0, 0, 0
        if entry_name in self._image_data:
            _, w, h, depth = self._image_data[entry_name]
            score = self._compute_filename_score(entry_name)

        self._queries.save_cover_label_v2(
            asset_id=asset.id,
            image_name=entry_name,
            image_width=w,
            image_height=h,
            archive_depth=depth,
            filename_score=score,
            images_shown=len(self._cards),
        )
        self._advance()

    def _compute_filename_score(self, name: str) -> int:
        name_lower = name.lower()
        score = 0
        if "main" in name_lower or "cover" in name_lower:
            score += 100
        if "preview" in name_lower or "thumb" in name_lower:
            score += 50
        if name_lower.startswith("00") or name_lower.startswith("01"):
            score += 80
        return score

    def _on_skip(self):
        self._advance()

    def _advance(self):
        self._current_idx += 1
        # Prefetch more when fewer than 10 remain
        if len(self._assets) - self._current_idx < 10:
            self._prefetch_more()
        if self._current_idx < len(self._assets):
            self._show_current()
        else:
            self._finish()

    def _finish(self):
        QMessageBox.information(
            self, "Complete",
            f"Labeled {self._current_idx} asset(s).\n\n"
            "Thumbnails will regenerate with your selections."
        )
        self.labeling_complete.emit()
        self.accept()

    def _on_done(self):
        if self._current_idx < len(self._assets):
            remaining = len(self._assets) - self._current_idx
            reply = QMessageBox.question(
                self, "Exit Early?",
                f"{remaining} asset(s) remaining.\n\nExit now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.labeling_complete.emit()
        self.accept()

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key_Escape:
            self._on_done()
        elif key == Qt.Key_S:
            self._on_skip()
        elif Qt.Key_1 <= key <= Qt.Key_6:
            idx = key - Qt.Key_1
            if idx < len(self._cards):
                self._save_and_advance(self._cards[idx]._entry_name)
        else:
            super().keyPressEvent(event)
