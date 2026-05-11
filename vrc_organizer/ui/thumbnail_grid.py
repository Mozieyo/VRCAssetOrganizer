from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QAbstractListModel, QModelIndex, Signal, QSize, QRect, QPoint,
)
from PySide6.QtGui import QPainter, QPixmap, QColor, QFont, QPen, QBrush, QFontMetrics, QPalette
from PySide6.QtWidgets import (
    QListView, QStyledItemDelegate, QStyle, QWidget, QVBoxLayout,
)

from vrc_organizer.database.queries import Queries

PAGE_SIZE = 100
THUMB_SIZE = 192
MAX_CACHED_THUMBS = 200
CARD_PADDING = 8
LABEL_HEIGHT = 28


class AssetListModel(QAbstractListModel):
    data_changed_full = Signal()

    def __init__(self, queries: Queries, parent=None):
        super().__init__(parent)
        self._queries = queries
        self._ids: list[int] = []
        self._total = 0
        self._filetypes: Optional[list[str]] = None
        self._or_tag_ids: Optional[list[int]] = None
        self._and_tag_ids: Optional[list[int]] = None
        self._search: Optional[str] = None
        self._sort = "date_added DESC"
        self._pixmap_cache: dict[int, QPixmap] = {}
        self._thumb_size = THUMB_SIZE

    def rowCount(self, parent=QModelIndex()):
        return len(self._ids)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._ids):
            return None

        aid = self._ids[row]
        if role == Qt.UserRole:
            return aid
        if role == Qt.DisplayRole:
            # Fetch just the filename — fast single-row lookup
            return self._get_filename(aid)
        if role == Qt.DecorationRole:
            return self._get_thumb(aid)
        if role == Qt.ToolTipRole:
            return self._get_tooltip(aid)
        return None

    def canFetchMore(self, index):
        return len(self._ids) < self._total

    def fetchMore(self, index):
        new_ids = self._queries.list_assets(
            offset=len(self._ids), limit=PAGE_SIZE,
            filetypes=self._filetypes, or_tag_ids=self._or_tag_ids,
            and_tag_ids=self._and_tag_ids,
            search_query=self._search, sort=self._sort,
        )
        if new_ids:
            start = len(self._ids)
            self.beginInsertRows(QModelIndex(), start, start + len(new_ids) - 1)
            self._ids.extend(a.id for a in new_ids)
            self.endInsertRows()

    def refresh(self):
        self.beginResetModel()
        self._ids.clear()
        self._pixmap_cache.clear()
        self._total = self._queries.count_assets(
            filetypes=self._filetypes, or_tag_ids=self._or_tag_ids,
            and_tag_ids=self._and_tag_ids, search_query=self._search,
        )
        self.endResetModel()
        self.data_changed_full.emit()

    def set_filter(self, filetypes: Optional[list[str]] = None,
                   or_tag_ids: Optional[list[int]] = None,
                   and_tag_ids: Optional[list[int]] = None,
                   search: Optional[str] = None):
        self._filetypes = filetypes
        self._or_tag_ids = or_tag_ids
        self._and_tag_ids = and_tag_ids
        self._search = search
        self.refresh()

    def get_asset_id(self, index: int) -> int:
        return self._ids[index] if 0 <= index < len(self._ids) else 0

    # ── internal helpers ──
    def _get_filename(self, aid: int) -> str:
        a = self._queries.get_asset(aid)
        return a.filename if a else "..."

    def set_thumb_size(self, size: int):
        self._thumb_size = size

    def _get_thumb(self, aid: int) -> Optional[QPixmap]:
        cached = self._pixmap_cache.get(aid)
        if cached is not None:
            ts = self._thumb_size
            if cached.width() == ts or cached.height() == ts:
                return cached
            # Wrong size — evict and reload below
            del self._pixmap_cache[aid]
        try:
            a = self._queries.get_asset(aid)
            if a is None or a.thumbnail is None:
                return None
            pix = QPixmap()
            if pix.load(str(a.thumbnail)):
                ts = self._thumb_size
                scaled = pix.scaled(ts, ts, Qt.KeepAspectRatio,
                                    Qt.SmoothTransformation)
                # Evict oldest if at capacity (Python 3.7+ dicts preserve insertion order)
                while len(self._pixmap_cache) >= MAX_CACHED_THUMBS:
                    oldest = next(iter(self._pixmap_cache))
                    del self._pixmap_cache[oldest]
                self._pixmap_cache[aid] = scaled
                return scaled
        except Exception:
            pass
        return None

    def _get_tooltip(self, aid: int) -> str:
        a = self._queries.get_asset(aid)
        if a is None:
            return ""
        size_mb = a.file_size / (1024 * 1024)
        return f"{a.filename}\nType: {a.filetype}\nSize: {size_mb:.1f} MB"


class ThumbnailDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumb_size = THUMB_SIZE
        self._placeholder_font = QFont()
        self._placeholder_font.setPixelSize(32)
        self._label_font = QFont()
        self._label_font.setPixelSize(12)

    def set_thumb_size(self, size: int):
        self._thumb_size = size

    def paint(self, painter: QPainter, option, index: QModelIndex):
        try:
            self._paint(painter, option, index)
        except Exception:
            painter.restore()

    def _paint(self, painter: QPainter, option, index: QModelIndex):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        rect = option.rect
        card_rect = QRect(
            rect.x() + CARD_PADDING, rect.y() + CARD_PADDING,
            rect.width() - CARD_PADDING * 2, rect.height() - CARD_PADDING * 2,
        )

        palette = option.palette

        # Selection highlight
        if option.state & QStyle.State_Selected:
            highlight = palette.color(QPalette.ColorRole.Highlight)
            highlight.setAlpha(60)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(highlight))
            painter.drawRoundedRect(card_rect.adjusted(-2, -2, 2, 2), 8, 8)

        # Thumbnail area
        thumb_rect = QRect(card_rect.x(), card_rect.y(),
                           card_rect.width(), card_rect.height() - LABEL_HEIGHT)

        pixmap = index.data(Qt.DecorationRole)
        if pixmap and not pixmap.isNull():
            pw = min(pixmap.width(), thumb_rect.width())
            ph = min(pixmap.height(), thumb_rect.height())
            px = thumb_rect.x() + (thumb_rect.width() - pw) // 2
            py = thumb_rect.y() + (thumb_rect.height() - ph) // 2
            painter.drawPixmap(px, py, pw, ph, pixmap)
        else:
            placeholder_bg = palette.color(QPalette.ColorRole.Midlight)
            placeholder_fg = palette.color(QPalette.ColorRole.PlaceholderText)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(placeholder_bg))
            painter.drawRoundedRect(thumb_rect, 6, 6)
            painter.setPen(placeholder_fg)
            painter.setFont(self._placeholder_font)
            painter.drawText(thumb_rect, Qt.AlignCenter, "?")

        # Filename label
        label_rect = QRect(card_rect.x(), thumb_rect.bottom() + 4,
                           card_rect.width(), LABEL_HEIGHT - 4)
        label_color = palette.color(QPalette.ColorRole.Text)
        painter.setPen(label_color)
        painter.setFont(self._label_font)
        fm = QFontMetrics(self._label_font)
        filename = index.data(Qt.DisplayRole) or ""
        elided = fm.elidedText(filename, Qt.ElideMiddle, label_rect.width())
        painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, elided)

        painter.restore()

    def sizeHint(self, option, index):
        total = self._thumb_size + LABEL_HEIGHT + CARD_PADDING * 2
        return QSize(total, total)


class AssetListView(QListView):
    files_dropped = Signal(list)
    drag_entered = Signal()
    drag_left = Signal()
    delete_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setMovement(QListView.Static)
        self.setResizeMode(QListView.Adjust)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setWrapping(True)
        self.setFlow(QListView.LeftToRight)
        self.setSpacing(8)
        self.setWordWrap(True)
        self.setBatchSize(50)

    def setModel(self, model):
        super().setModel(model)
        if model is not None:
            model.modelReset.connect(self._update_empty_state)
            model.rowsInserted.connect(self._update_empty_state)
            model.rowsRemoved.connect(self._update_empty_state)

    def _update_empty_state(self):
        self.viewport().update()

    def paintEvent(self, event):
        try:
            super().paintEvent(event)
        except Exception:
            pass  # don't crash on paint failures
        try:
            model = self.model()
            if model is not None and model.rowCount() == 0:
                painter = QPainter(self.viewport())
                painter.setRenderHint(QPainter.Antialiasing)
                font = QFont()
                font.setPixelSize(16)
                painter.setFont(font)
                painter.setPen(QColor(148, 163, 184))
                painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                               "Drag files here to get started\nor use File > Import")
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Only recenter if we're not already in a recenter call (prevents
        # infinite recursion: resizeEvent → recenter → setViewportMargins →
        # resizeEvent → ...)
        if not getattr(self, '_in_recenter', False):
            self.recenter()

    def recenter(self):
        if getattr(self, '_in_recenter', False):
            return
        self._in_recenter = True
        try:
            self._do_recenter()
        finally:
            self._in_recenter = False

    def _do_recenter(self):
        if not self.model() or self.model().rowCount() == 0:
            self.setViewportMargins(0, 0, 0, 0)
            return
        view_w = self.viewport().width()
        if view_w <= 0:
            return
        item_w = self.sizeHintForColumn(0)
        spacing = self.spacing()
        pitch = item_w + spacing
        if pitch <= 0 or view_w <= pitch:
            self.setViewportMargins(0, 0, 0, 0)
            return
        fit_cols = max(1, view_w // pitch)
        used_w = fit_cols * pitch - spacing
        margin = (view_w - used_w) // 2
        if margin < 0:
            margin = 0
        cur = self.viewportMargins()
        if cur.left() != margin:
            self.setViewportMargins(margin, 0, margin, 0)

    def select_asset_ids(self, asset_ids: list[int]):
        """Select items matching the given asset IDs."""
        if not asset_ids:
            return
        model = self.model()
        if model is None:
            return
        sel = self.selectionModel()
        target = set(asset_ids)
        for row in range(model.rowCount()):
            aid = model.index(row, 0).data(Qt.UserRole)
            if aid in target:
                sel.select(model.index(row, 0), sel.Select)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        pos = event.pos()
        index = self.indexAt(pos)
        if index.isValid():
            self.setCurrentIndex(index)
        super().contextMenuEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            self.drag_entered.emit()
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.drag_left.emit()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.drag_left.emit()
        if event.mimeData().hasUrls():
            paths = [url.toLocalFile() for url in event.mimeData().urls()]
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
