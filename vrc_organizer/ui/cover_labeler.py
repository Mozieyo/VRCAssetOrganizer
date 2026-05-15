"""Cover Labeler — fast captcha-style UI for selecting the best thumbnail."""
from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QWidget, QFrame, QMessageBox,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.models.asset import Asset
from vrc_organizer.scanner.unitypackage import _read_guid_pathnames

CARD_SIZE = 140
MAX_CARDS = 6
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
MAX_FILE = 30 * 1024 * 1024


def _score(name: str, size: int) -> int:
    """Score image for thumbnail quality. Higher = better."""
    n = name.lower()
    base = n.rsplit("/", 1)[-1] if "/" in n else n

    # Reject technical textures
    if any(x in n for x in ("_n.", "_normal", "_mask", "_ao", "_metallic", "_rough", "_spec", "_bump")):
        return -100
    if "uv" in base and "uvs" not in base:
        return -80

    s = 0
    if base in ("main.png", "main.jpg", "cover.png", "cover.jpg"):
        s += 100
    elif base.startswith("00") or base.startswith("01"):
        s += 90
    elif "preview" in n or "thumb" in n or "eyecatch" in n:
        s += 80
    elif any(x in n for x in ("body", "face", "skin", "character")):
        if "nobody" not in n:
            s += 60
    elif any(x in n for x in ("diffuse", "albedo", "basecolor")):
        s += 50

    if size > 1_000_000:
        s += 20
    elif size > 500_000:
        s += 10

    depth = n.count("/") + n.count("\\")
    if depth <= 1:
        s += 15
    elif depth > 4:
        s -= 10

    return s


def _dims(data: bytes) -> tuple[int, int]:
    try:
        img = Image.open(io.BytesIO(data))
        return img.size
    except Exception:
        return 0, 0


def _discover(asset: Asset, cache_dir: Path | None) -> list[tuple[str, bytes, int, int]]:
    """Find candidate images. Returns [(path, data, w, h), ...]."""
    fp = asset.filepath
    results: list[tuple[str, bytes, int, int, int]] = []  # (path, data, w, h, score)

    def add(path: str, data: bytes):
        w, h = _dims(data)
        sc = _score(path, len(data))
        if sc > -50:
            results.append((path, data, w, h, sc))

    if fp.is_dir():
        for f in fp.rglob("*"):
            if f.suffix.lower() in IMAGE_EXTS and f.is_file():
                try:
                    sz = f.stat().st_size
                    if sz < MAX_FILE:
                        add(f.relative_to(fp).as_posix(), f.read_bytes())
                except OSError:
                    pass
    elif fp.suffix.lower() == ".zip" and fp.exists():
        try:
            with zipfile.ZipFile(fp, "r") as zf:
                for info in zf.infolist():
                    if not info.is_dir() and info.file_size < MAX_FILE:
                        if any(info.filename.lower().endswith(e) for e in IMAGE_EXTS):
                            try:
                                add(info.filename, zf.read(info))
                            except Exception:
                                pass
        except Exception:
            pass
    elif fp.suffix.lower() == ".unitypackage" and fp.exists():
        try:
            tf = tarfile.open(fp, "r:gz")
            try:
                guid_map = _read_guid_pathnames(tf)
                for m in tf.getmembers():
                    if not m.isfile() or m.size > MAX_FILE:
                        continue
                    if m.name.endswith("/preview.png"):
                        try:
                            f = tf.extractfile(m)
                            if f:
                                add(f"[preview] {m.name}", f.read())
                        except Exception:
                            pass
                    elif m.name.endswith("/asset"):
                        guid = m.name.split("/", 1)[0]
                        pn = guid_map.get(guid, "")
                        if pn and any(pn.lower().endswith(e) for e in IMAGE_EXTS):
                            try:
                                f = tf.extractfile(m)
                                if f:
                                    add(pn, f.read())
                            except Exception:
                                pass
            finally:
                tf.close()
        except Exception:
            pass

    # Fallback: cached thumbnail
    if not results and cache_dir:
        fb = cache_dir / f"{asset.id}.png"
        if fb.exists():
            try:
                data = fb.read_bytes()
                w, h = _dims(data)
                results.append(("__cached__", data, w, h, 0))
            except Exception:
                pass

    # Sort by score, take top N
    results.sort(key=lambda x: -x[4])
    return [(p, d, w, h) for p, d, w, h, _ in results[:MAX_CARDS]]


class CoverLabelerDialog(QDialog):
    """Fast cover selection. 1-6 or click to select, S = skip, Esc = done."""
    labeling_complete = Signal()

    def __init__(self, queries: Queries, thumb_cache_dir: Path = None, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._cache = thumb_cache_dir
        self._assets: list[Asset] = []
        self._idx = 0
        self._candidates: list[tuple[str, bytes, int, int]] = []
        self._cards: list[QWidget] = []
        self._labeled = 0

        self.setWindowTitle("Select Cover")
        self.setMinimumSize(600, 420)
        self._build_ui()
        self._load_queue()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 12, 16, 12)

        # Top bar
        top = QHBoxLayout()
        self._progress = QLabel("Loading...")
        self._progress.setStyleSheet("font-weight: bold; font-size: 14px;")
        top.addWidget(self._progress)
        top.addStretch()
        hints = QLabel("1-6=Select  S=Skip  Esc=Done")
        hints.setStyleSheet("color: #64748b; font-size: 11px;")
        top.addWidget(hints)
        root.addLayout(top)

        # Filename
        self._filename = QLabel()
        self._filename.setWordWrap(True)
        self._filename.setStyleSheet("color: #94a3b8; font-size: 12px;")
        root.addWidget(self._filename)

        # Grid
        grid_frame = QFrame()
        grid_frame.setStyleSheet("background: #0f172a; border-radius: 4px;")
        self._grid = QGridLayout(grid_frame)
        self._grid.setSpacing(12)
        self._grid.setContentsMargins(16, 16, 16, 16)
        root.addWidget(grid_frame, 1)

        # Bottom
        btm = QHBoxLayout()
        self._status = QLabel()
        self._status.setStyleSheet("color: #64748b; font-size: 11px;")
        btm.addWidget(self._status)
        btm.addStretch()
        skip = QPushButton("Skip")
        skip.setMinimumWidth(70)
        skip.clicked.connect(self._skip)
        btm.addWidget(skip)
        done = QPushButton("Done")
        done.setMinimumWidth(70)
        done.clicked.connect(self._finish)
        btm.addWidget(done)
        root.addLayout(btm)

    def _load_queue(self):
        self._assets = self._queries.get_unlabeled_cover_assets(limit=200)
        if not self._assets:
            self._progress.setText("All done!")
            self._status.setText("No assets need cover labels.")
            return
        self._idx = 0
        self._show()

    def _show(self):
        # Tear down the previous batch of cards completely before showing the
        # next asset. Detach from grid layout BEFORE deleteLater so a stray
        # late mouse event can't fire on a half-deleted widget — that's the
        # most likely culprit for the "crash after 23 items" report we
        # couldn't get a traceback for.
        try:
            while self._grid.count():
                it = self._grid.takeAt(0)
                w = it.widget() if it else None
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            self._cards.clear()
            self._candidates.clear()
        except Exception:
            # Even if cleanup throws (rare Qt edge cases), keep going so the
            # session doesn't dead-end on the user.
            self._cards.clear()
            self._candidates.clear()

        if self._idx >= len(self._assets):
            self._finish()
            return

        asset = self._assets[self._idx]
        self._progress.setText(f"{self._idx + 1} / {len(self._assets)}")
        self._filename.setText(asset.filename)

        # Discover images
        try:
            self._candidates = _discover(asset, self._cache)
        except Exception:
            self._candidates = []

        if not self._candidates:
            self._status.setText("No images found")
            return

        for i, (path, data, w, h) in enumerate(self._candidates):
            try:
                card = self._make_card(i, data, w, h)
                row, col = divmod(i, 3)
                self._grid.addWidget(card, row, col)
                self._cards.append(card)
            except Exception:
                # A single bad candidate shouldn't kill the whole row.
                continue

        self._status.setText(f"{len(self._candidates)} image(s)")

    def _make_card(self, idx: int, data: bytes, w: int, h: int) -> QWidget:
        card = QFrame()
        card.setFixedSize(CARD_SIZE + 16, CARD_SIZE + 32)
        card.setCursor(Qt.PointingHandCursor)
        card.setStyleSheet("""
            QFrame { background: #1e293b; border: 2px solid #334155; border-radius: 5px; }
            QFrame:hover { border-color: #3b82f6; background: #1e3a5f; }
        """)
        card.mousePressEvent = lambda e, i=idx: self._select(i)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)

        # Image
        img_lbl = QLabel()
        img_lbl.setAlignment(Qt.AlignCenter)
        img_lbl.setFixedSize(CARD_SIZE, CARD_SIZE)
        pix = QPixmap()
        pix.loadFromData(data)
        if not pix.isNull():
            img_lbl.setPixmap(pix.scaled(CARD_SIZE, CARD_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            img_lbl.setText("?")
            img_lbl.setStyleSheet("color: #64748b; font-size: 24px;")

        # Badge
        badge = QLabel(str(idx + 1))
        badge.setFixedSize(24, 24)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet("""
            background: #3b82f6; color: white; font-weight: bold;
            border-radius: 6px; font-size: 12px;
        """)
        badge.setParent(img_lbl)
        badge.move(4, 4)

        layout.addWidget(img_lbl)

        # Dims
        if w and h:
            dim = QLabel(f"{w}x{h}")
            dim.setAlignment(Qt.AlignCenter)
            dim.setStyleSheet("color: #64748b; font-size: 10px;")
            layout.addWidget(dim)

        return card

    def _select(self, idx: int):
        if idx >= len(self._candidates) or self._idx >= len(self._assets):
            return

        asset = self._assets[self._idx]
        path, _, w, h = self._candidates[idx]
        depth = path.count("/") + path.count("\\")
        score = _score(path, 0)

        self._queries.save_cover_label_v2(
            asset_id=asset.id,
            image_name=path,
            image_width=w,
            image_height=h,
            archive_depth=depth,
            filename_score=score,
            images_shown=len(self._candidates),
        )
        self._labeled += 1
        self._next()

    def _skip(self):
        """Record a permanent skip so the asset stops re-appearing.

        Stored as `image_name='__skipped__'` which the unlabeled-cover query
        already excludes (any row in cover_labels_v2 is treated as labeled).
        """
        if self._idx >= len(self._assets):
            return
        asset = self._assets[self._idx]
        self._queries.save_cover_label_v2(
            asset_id=asset.id,
            image_name="__skipped__",
            images_shown=len(self._candidates),
        )
        self._next()

    def _next(self):
        self._idx += 1
        if len(self._assets) - self._idx < 10:
            more = self._queries.get_unlabeled_cover_assets(limit=100)
            existing = {a.id for a in self._assets}
            self._assets.extend(a for a in more if a.id not in existing)
        self._show()

    def _finish(self):
        if self._labeled:
            QMessageBox.information(self, "Done", f"Labeled {self._labeled} cover(s).")
        self.labeling_complete.emit()
        self.accept()

    def keyPressEvent(self, e: QKeyEvent):
        k = e.key()
        if k == Qt.Key_Escape:
            self._finish()
        elif k == Qt.Key_S:
            self._skip()
        elif Qt.Key_1 <= k <= Qt.Key_6:
            self._select(k - Qt.Key_1)
        else:
            super().keyPressEvent(e)
