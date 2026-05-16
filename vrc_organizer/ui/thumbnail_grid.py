from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QAbstractListModel, QModelIndex, Signal, QSize, QRect, QPoint, QTimer,
    QItemSelectionModel, QEvent, QSettings,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QColor, QFont, QPen, QBrush, QFontMetrics, QPalette,
    QMouseEvent, QKeyEvent,
)
from PySide6.QtWidgets import (
    QWidget, QScrollArea, QFrame, QVBoxLayout, QApplication, QSizePolicy,
)

from vrc_organizer.database.queries import Queries
from vrc_organizer.romaji import has_japanese, to_romaji


# Cached "show romaji" flag — paintEvent runs once per visible card per
# scroll/redraw, and re-instantiating QSettings each time burned measurable
# CPU on libraries of a few hundred assets. The toggle in the View menu
# calls set_show_romaji_cached() to invalidate this.
_SHOW_ROMAJI_CACHE: bool | None = None


def _show_romaji_cached() -> bool:
    global _SHOW_ROMAJI_CACHE
    if _SHOW_ROMAJI_CACHE is None:
        _SHOW_ROMAJI_CACHE = bool(QSettings().value("show_romaji", True, type=bool))
    return _SHOW_ROMAJI_CACHE


def set_show_romaji_cached(value: bool) -> None:
    global _SHOW_ROMAJI_CACHE
    _SHOW_ROMAJI_CACHE = bool(value)

PAGE_SIZE = 100
THUMB_SIZE = 192
# Pixmap cache cap: each scaled QPixmap is roughly card_w^2 * 4 bytes
# (~160 KB at default density). 100 entries ≈ 16 MB. The visible window
# plus buffer is usually 50-80 cards; a 100-entry cap keeps scroll-back
# snappy without holding a large pile of off-screen pixmaps.
MAX_CACHED_THUMBS = 100
CARD_PADDING = 8
LABEL_HEIGHT = 44  # taller label band so a 2-line (romaji + name) layout fits without clipping


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
        self._asset_cache: dict[int, object] = {}
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
            return self._get_filename(aid)
        if role == Qt.DecorationRole:
            return self._get_thumb(aid)
        if role == Qt.ToolTipRole:
            return self._get_tooltip(aid)
        return None

    def canFetchMore(self, index):
        # refresh() now loads every matching id up-front, so the view never
        # needs to paginate. Kept as a no-op for any external caller still
        # checking the standard QAbstractListModel pagination contract.
        return False

    def fetchMore(self, index):
        return

    def refresh(self):
        # Note: _pixmap_cache and _asset_cache are keyed by stable asset_id,
        # not by row position. A filter change reorders/shrinks the visible
        # set but doesn't invalidate the cached entries — so we keep them
        # and let the bounded eviction (MAX_CACHED_THUMBS) manage memory.
        # This makes "clear filters → show everything" near-instant on
        # libraries the user has already been scrolling through.
        #
        # We pull every matching id in one SQL round trip instead of
        # paginating PAGE_SIZE at a time via fetchMore — on libraries with
        # thousands of assets the old approach issued ~50 LIMIT/OFFSET
        # queries per filter change.
        self.beginResetModel()
        self._ids = self._queries.list_asset_ids(
            filetypes=self._filetypes, or_tag_ids=self._or_tag_ids,
            and_tag_ids=self._and_tag_ids, search_query=self._search,
            sort=self._sort,
        )
        self._total = len(self._ids)
        # Prime the per-asset cache for any ids that aren't already there.
        # Without this, _refresh_card_data in the view would trigger one
        # SELECT per card the first time around — a fresh "show all 2000"
        # used to fan out into 2000 round trips.
        missing = [aid for aid in self._ids if aid not in self._asset_cache]
        if missing:
            self._asset_cache.update(self._queries.get_assets_by_ids(missing))
        self.endResetModel()
        self.data_changed_full.emit()

    def invalidate_caches(self):
        """Drop the pixmap + asset caches. Call when an asset's filename,
        thumbnail, or other field changes, OR when the thumb pixel size
        is being changed and we want fresh resolutions everywhere."""
        self._pixmap_cache.clear()
        self._asset_cache = {}

    def refresh_asset(self, asset_id: int):
        """Mark a single asset's cached data as stale and tell the view
        to redraw just that row. Use this when an asset's row data
        changed (thumbnail, notes, tag set without filter implications)
        but its position in the filtered set is stable. Much cheaper
        than refresh() — no SQL count/select runs."""
        self._asset_cache.pop(asset_id, None)
        self._pixmap_cache.pop(asset_id, None)
        try:
            row = self._ids.index(asset_id)
        except ValueError:
            # Asset isn't in the current filtered set; nothing to redraw.
            return
        idx = self.index(row, 0)
        self.dataChanged.emit(idx, idx, [])

    def refresh_assets(self, asset_ids: list[int]):
        """Bulk variant of refresh_asset for batches like thumb worker
        completions. Only the rows that are actually in the current
        result set get their dataChanged signal."""
        for aid in asset_ids:
            self.refresh_asset(aid)

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

    def _get_asset_cached(self, aid: int):
        if aid not in self._asset_cache:
            self._asset_cache[aid] = self._queries.get_asset(aid)
        return self._asset_cache[aid]

    def _get_filename(self, aid: int) -> str:
        a = self._get_asset_cached(aid)
        return a.filename if a else "..."

    def set_thumb_size(self, size: int):
        if size != self._thumb_size:
            self._thumb_size = size
            self._pixmap_cache.clear()

    def _get_thumb(self, aid: int) -> Optional[QPixmap]:
        cached = self._pixmap_cache.get(aid)
        if cached is not None:
            return cached
        a = self._get_asset_cached(aid)
        if a is None or a.thumbnail is None:
            return None
        pix = QPixmap()
        if not pix.load(str(a.thumbnail)):
            return None
        ts = self._thumb_size
        scaled = pix.scaled(ts, ts, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        while len(self._pixmap_cache) >= MAX_CACHED_THUMBS:
            oldest = next(iter(self._pixmap_cache))
            del self._pixmap_cache[oldest]
        self._pixmap_cache[aid] = scaled
        return scaled

    def _get_tooltip(self, aid: int) -> str:
        a = self._get_asset_cached(aid)
        if a is None:
            return ""
        size_mb = a.file_size / (1024 * 1024)
        return f"{a.filename}\nType: {a.filetype}\nSize: {size_mb:.1f} MB"


class AssetCard(QFrame):
    """Single asset tile — manually painted thumbnail + filename label."""
    clicked = Signal(int, object)  # asset_id, Qt.KeyboardModifiers
    double_clicked = Signal(int)
    context_menu = Signal(int, QPoint)  # asset_id, global pos

    def __init__(self, asset_id: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._asset_id = asset_id
        self._selected = False
        self._pixmap: QPixmap | None = None
        self._filename = ""
        self.setMouseTracking(False)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_StyledBackground, False)

    @property
    def asset_id(self) -> int:
        return self._asset_id

    def rebind(self, asset_id: int):
        """Reassign this card to a different asset. Used by the view's
        recycler so a filter change doesn't have to destroy and recreate
        QFrames — the same widget is repointed at a new row instead."""
        if asset_id == self._asset_id:
            return
        self._asset_id = asset_id
        self._pixmap = None
        self._filename = ""
        self._selected = False
        self.update()

    def set_data(self, filename: str, pixmap: QPixmap | None, tooltip: str = ""):
        if filename != self._filename or pixmap is not self._pixmap:
            self._filename = filename
            self._pixmap = pixmap
            self.update()
        self.setToolTip(tooltip)

    def set_selected(self, sel: bool):
        if self._selected != sel:
            self._selected = sel
            self.update()

    def mousePressEvent(self, event: QMouseEvent):
        # IMPORTANT: don't call super() on left/right press — the default
        # QWidget behavior is to ignore the event, which would bubble it up
        # to the grid container and trigger a marquee-start that clears the
        # selection we *just* set. Accept the event ourselves.
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._asset_id, event.modifiers())
            event.accept()
            return
        if event.button() == Qt.RightButton:
            self.context_menu.emit(self._asset_id, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        # Swallow stray moves so a click-with-tiny-drag doesn't escape into
        # the container as a marquee.
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit(self._asset_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)

            palette = self.palette()
            is_dark = palette.color(QPalette.Window).lightness() < 128
            if is_dark:
                card_bg = QColor(30, 41, 59)
                card_border = QColor(51, 65, 85)
                label_color = QColor(226, 232, 240)
                placeholder_bg = QColor(15, 23, 42)
                placeholder_fg = QColor(100, 116, 139)
            else:
                card_bg = QColor(255, 255, 255)
                card_border = QColor(203, 213, 225)
                label_color = QColor(15, 23, 42)
                placeholder_bg = QColor(241, 245, 249)
                placeholder_fg = QColor(100, 116, 139)

            rect = self.rect()
            p.setPen(QPen(card_border, 1))
            p.setBrush(QBrush(card_bg))
            p.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 10, 10)

            if self._selected:
                sel = QColor(59, 130, 246)
                bg = QColor(sel)
                bg.setAlpha(40)
                p.setPen(QPen(sel, 2))
                p.setBrush(QBrush(bg))
                p.drawRoundedRect(rect.adjusted(1, 1, -2, -2), 9, 9)

            inner_pad = 6
            thumb_rect = QRect(
                inner_pad, inner_pad,
                rect.width() - 2 * inner_pad,
                rect.height() - LABEL_HEIGHT - inner_pad,
            )

            if self._pixmap is not None and not self._pixmap.isNull():
                scaled = self._pixmap.scaled(
                    thumb_rect.width(), thumb_rect.height(),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
                px = thumb_rect.x() + (thumb_rect.width() - scaled.width()) // 2
                py = thumb_rect.y() + (thumb_rect.height() - scaled.height()) // 2
                p.drawPixmap(px, py, scaled)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(placeholder_bg))
                p.drawRoundedRect(thumb_rect, 6, 6)
                p.setPen(placeholder_fg)
                f = QFont()
                f.setPixelSize(28)
                f.setWeight(QFont.Light)
                p.setFont(f)
                p.drawText(thumb_rect, Qt.AlignCenter, "?")

            # Filename line + optional romaji line ("furigana" style).
            jp = _show_romaji_cached() and has_japanese(self._filename)

            f = QFont()
            f.setPixelSize(11)
            p.setFont(f)
            p.setPen(label_color)
            fm = QFontMetrics(f)

            if jp:
                # Two-line layout: tiny romaji on top, primary filename below.
                # Geometry is anchored from the bottom of the card so the
                # filename never gets clipped by the label band.
                small_f = QFont()
                small_f.setPixelSize(9)
                small_fm = QFontMetrics(small_f)
                romaji_h = small_fm.height()
                name_h = fm.height()
                # Place the filename right above the bottom edge, then
                # stack the romaji just above it.
                name_rect = QRect(
                    inner_pad,
                    rect.height() - name_h - 4,
                    rect.width() - 2 * inner_pad,
                    name_h,
                )
                romaji_rect = QRect(
                    inner_pad,
                    name_rect.top() - romaji_h - 1,
                    rect.width() - 2 * inner_pad,
                    romaji_h,
                )
                p.setFont(small_f)
                p.setPen(placeholder_fg)
                romaji_text = small_fm.elidedText(
                    to_romaji(self._filename), Qt.ElideMiddle, romaji_rect.width()
                )
                p.drawText(romaji_rect, Qt.AlignHCenter | Qt.AlignTop, romaji_text)

                p.setFont(f)
                p.setPen(label_color)
                elided = fm.elidedText(self._filename, Qt.ElideMiddle, name_rect.width())
                p.drawText(name_rect, Qt.AlignHCenter | Qt.AlignTop, elided)
            else:
                label_rect = QRect(
                    inner_pad,
                    thumb_rect.bottom() + 4,
                    rect.width() - 2 * inner_pad,
                    LABEL_HEIGHT - 6,
                )
                elided = fm.elidedText(self._filename, Qt.ElideMiddle, label_rect.width())
                p.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, elided)
        finally:
            p.end()


class _MarqueeOverlay(QWidget):
    """Transparent overlay that paints the rubber-band rectangle on top of
    the cards. Lives as a child of _GridContainer with `raise_()` after each
    relayout, so it always sits in front of card widgets z-order-wise.

    It's transparent for mouse events — the click that started the marquee
    goes to the container underneath, not to this overlay.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._rect = QRect()

    def set_rect(self, rect: QRect):
        if rect == self._rect:
            return
        self._rect = rect
        self.update()

    def paintEvent(self, event):
        if self._rect.isNull():
            return
        p = QPainter(self)
        try:
            sel = QColor(59, 130, 246)
            fill = QColor(sel)
            fill.setAlpha(50)
            p.setPen(QPen(sel, 1))
            p.setBrush(QBrush(fill))
            p.drawRect(self._rect)
        finally:
            p.end()


class _GridContainer(QWidget):
    """Scrollable child of AssetListView. Owns the rubber-band logic; the
    visible rectangle is painted by a sibling overlay so it draws on top of
    the cards, not behind them.
    """

    marquee_changed = Signal(QRect, object)  # rect, Qt.KeyboardModifiers

    def __init__(self, view: "AssetListView"):
        super().__init__()
        self._view = view
        self._marquee_origin: QPoint | None = None
        self._marquee_rect: QRect = QRect()
        self.overlay = _MarqueeOverlay(self)
        self.overlay.hide()
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the overlay sized to the container so the marquee can extend
        # anywhere on screen.
        self.overlay.setGeometry(0, 0, self.width(), self.height())

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._marquee_origin = e.position().toPoint()
            self._marquee_rect = QRect(self._marquee_origin, self._marquee_origin)
            if not (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                self._view.clear_selection()
            self.overlay.set_rect(self._marquee_rect)
            self.overlay.show()
            self.overlay.raise_()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._marquee_origin is not None:
            self._marquee_rect = QRect(
                self._marquee_origin, e.position().toPoint()
            ).normalized()
            self.overlay.set_rect(self._marquee_rect)
            self.marquee_changed.emit(self._marquee_rect, e.modifiers())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._marquee_origin is not None and e.button() == Qt.LeftButton:
            self._marquee_origin = None
            self._marquee_rect = QRect()
            self.overlay.set_rect(QRect())
            self.overlay.hide()
            e.accept()
            return
        super().mouseReleaseEvent(e)


class AssetListView(QScrollArea):
    """Custom asset grid. Cards fill the row evenly with no QListView jitter."""

    files_dropped = Signal(list)
    drag_entered = Signal()
    drag_left = Signal()
    delete_requested = Signal()
    selection_changed = Signal()
    customContextMenuRequested = Signal(QPoint)
    doubleClicked = Signal(QModelIndex)

    SPACING = 10
    MIN_CARD_W = 80
    MAX_CARD_W = 360
    # How many extra rows of cards to keep rendered above/below the
    # viewport. A small buffer hides fly-in latency when the user scrolls;
    # too large and we lose the virtualization win.
    BUFFER_ROWS = 3
    # Hard cap on the QFrame pool, regardless of library size. Bigger than
    # any reasonable viewport + buffer can need, but tiny compared to
    # holding one widget per asset.
    POOL_CAP = 200

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model: AssetListModel | None = None
        # Pool of card widgets we recycle. `_row_to_card` is the active
        # binding; `_free_cards` holds the leftover pool entries waiting
        # to be assigned to a row. Together they always partition the
        # entries of self._cards — if a card is hidden but not in either
        # one, _update_visible_window can't find it and we leak widgets
        # while the grid renders empty.
        self._cards: list[AssetCard] = []
        self._row_to_card: dict[int, AssetCard] = {}
        self._free_cards: list[AssetCard] = []
        self._selected: set[int] = set()
        self._pre_marquee_selection: set[int] = set()
        self._last_click_aid: int | None = None
        self._density = 5
        self._card_w = self.MIN_CARD_W
        self._card_h = self.MIN_CARD_W + LABEL_HEIGHT
        self._cols = 1
        self._outer = self.SPACING
        self._relayout_pending = False
        self._show_empty_text = True

        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self._container = _GridContainer(self)
        self._container.marquee_changed.connect(self._apply_marquee)
        self.setWidget(self._container)

        # Scroll-driven virtualization: every scroll tick reconsiders
        # which rows fall inside the viewport and rebinds cards as needed.
        self.verticalScrollBar().valueChanged.connect(
            lambda _: self._update_visible_window()
        )

    # ── Model wiring ────────────────────────────────────────────────

    def setModel(self, model: AssetListModel):
        if self._model is not None:
            try:
                self._model.modelReset.disconnect(self._rebuild)
                self._model.rowsInserted.disconnect(self._on_rows_changed)
                self._model.rowsRemoved.disconnect(self._on_rows_changed)
                self._model.dataChanged.disconnect(self._on_data_changed)
            except (TypeError, RuntimeError):
                pass
        self._model = model
        model.modelReset.connect(self._rebuild)
        model.rowsInserted.connect(self._on_rows_changed)
        model.rowsRemoved.connect(self._on_rows_changed)
        model.dataChanged.connect(self._on_data_changed)
        self._rebuild()

    def model(self):
        return self._model

    def _on_rows_changed(self, *args):
        self._rebuild()

    def _on_data_changed(self, topLeft=None, bottomRight=None, *_):
        # Single-row change (refresh_asset path): rebind just that card
        # if it's currently visible. Multi-row falls back to refreshing
        # every bound card.
        if (topLeft is not None and bottomRight is not None
                and topLeft.row() == bottomRight.row()):
            row = topLeft.row()
            card = self._row_to_card.get(row)
            if card is not None and self._model is not None:
                aid = self._model.get_asset_id(row)
                card.rebind(aid)
                filename = self._model._get_filename(aid)
                pix = self._model._get_thumb(aid)
                tooltip = self._model._get_tooltip(aid)
                card.set_data(filename, pix, tooltip)
                card.set_selected(aid in self._selected)
            return
        self._refresh_card_data()

    def _ensure_all_rows_loaded(self):
        # No-op kept for source compatibility — the model now loads every
        # id in refresh() rather than paginating via fetchMore.
        return

    def _rebuild(self):
        # Filter changed / model reset / rows added/removed. We do NOT
        # clear _row_to_card — the cards bound to visible rows are still
        # useful; we just need to rebind their asset_id since model._ids
        # may have reshuffled. _update_visible_window picks that up by
        # checking each kept binding's asset_id against the current row.
        if getattr(self, "_rebuilding", False):
            return
        self._rebuilding = True
        try:
            if self._model is None:
                for c in self._cards:
                    c.setParent(None)
                    c.deleteLater()
                self._cards.clear()
                self._row_to_card.clear()
                self._free_cards.clear()
                self._schedule_relayout()
                return
            # Drop bindings for rows that no longer exist (model shrank).
            # Move the freed cards into the free pool so the allocator
            # can reuse them on the next visible-window update.
            n = self._model.rowCount()
            for row in list(self._row_to_card.keys()):
                if row >= n:
                    card = self._row_to_card.pop(row)
                    if card.isVisible():
                        card.hide()
                    self._free_cards.append(card)
            self._schedule_relayout()
        finally:
            self._rebuilding = False

    def _make_card(self) -> "AssetCard":
        card = AssetCard(0, self._container)
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)
        card.context_menu.connect(self._on_card_context_menu)
        return card

    def _refresh_card_data(self):
        """Pull fresh filename/pixmap/tooltip into every currently-bound
        card. Called when the data of the underlying assets changed but
        the row mapping didn't (e.g. background thumbnail worker writes
        a new path)."""
        if self._model is None:
            return
        for row, card in self._row_to_card.items():
            aid = card.asset_id
            filename = self._model._get_filename(aid)
            pix = self._model._get_thumb(aid)
            tooltip = self._model._get_tooltip(aid)
            card.set_data(filename, pix, tooltip)

    def _bind_card_to_row(self, card: "AssetCard", row: int):
        """Point a card at a model row, set its geometry and data, and
        ensure it's visible. Called from the virtualization update."""
        aid = self._model.get_asset_id(row)
        card.rebind(aid)
        grid_row, grid_col = divmod(row, max(1, self._cols))
        x = self._outer + grid_col * (self._card_w + self.SPACING)
        y = self.SPACING + grid_row * (self._card_h + self.SPACING)
        card.setGeometry(x, y, self._card_w, self._card_h)
        filename = self._model._get_filename(aid)
        pix = self._model._get_thumb(aid)
        tooltip = self._model._get_tooltip(aid)
        card.set_data(filename, pix, tooltip)
        card.set_selected(aid in self._selected)
        if not card.isVisible():
            card.show()

    def _visible_row_range(self) -> tuple[int, int]:
        """Return [first_idx, last_idx) of model rows that should currently
        have a card bound to them (visible viewport + a few buffer rows).
        Uses integer math against scroll position and card height."""
        if self._cols <= 0 or self._card_h <= 0 or self._model is None:
            return 0, 0
        row_h = self._card_h + self.SPACING
        scroll_y = self.verticalScrollBar().value()
        viewport_h = self.viewport().height()
        first_grid_row = max(0, (scroll_y - self.SPACING) // row_h - self.BUFFER_ROWS)
        last_grid_row = (scroll_y + viewport_h - self.SPACING) // row_h + self.BUFFER_ROWS
        n = self._model.rowCount()
        first_idx = int(first_grid_row * self._cols)
        last_idx = int(min(n, (last_grid_row + 1) * self._cols))
        return first_idx, max(first_idx, last_idx)

    def _update_visible_window(self):
        """Rebind cards in the pool so every row in the visible window has
        exactly one. Anything that fell out of the window gets recycled."""
        if self._model is None or self._cols <= 0:
            return
        first, last = self._visible_row_range()
        needed_rows = set(range(first, last))

        # Cards whose row scrolled out of the visible window: hide and
        # push onto the free pool for reuse below.
        for row in list(self._row_to_card.keys()):
            if row not in needed_rows:
                card = self._row_to_card.pop(row)
                if card.isVisible():
                    card.hide()
                self._free_cards.append(card)

        # Bind cards to every visible row. If a binding already exists
        # but the asset_id no longer matches (filter switched the ids
        # under us while preserving row numbers), rebind in place.
        for row in needed_rows:
            existing = self._row_to_card.get(row)
            if existing is not None:
                expected_aid = self._model.get_asset_id(row)
                if existing.asset_id != expected_aid:
                    self._bind_card_to_row(existing, row)
                continue
            if self._free_cards:
                card = self._free_cards.pop()
            elif len(self._cards) < self.POOL_CAP:
                card = self._make_card()
                self._cards.append(card)
            else:
                # Pool exhausted. Means BUFFER_ROWS * cols is too big for
                # POOL_CAP — practically impossible at sensible densities.
                break
            self._row_to_card[row] = card
            self._bind_card_to_row(card, row)

        # Keep the marquee overlay on top of any newly-shown cards.
        if hasattr(self._container, "overlay"):
            self._container.overlay.raise_()

    # ── Density + layout ────────────────────────────────────────────

    def set_density(self, density: int):
        self._density = max(1, min(10, density))
        self._schedule_relayout()

    def _target_min_card_w(self) -> int:
        span = self.MAX_CARD_W - self.MIN_CARD_W
        return self.MIN_CARD_W + (span * (self._density - 1)) // 9

    def _schedule_relayout(self):
        if self._relayout_pending:
            return
        self._relayout_pending = True
        QTimer.singleShot(0, self._do_relayout)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_relayout()

    def _do_relayout(self):
        self._relayout_pending = False
        # Stable base width — scrollbar may pop in/out, but viewport width
        # already reflects that. We use viewport because it ignores frame.
        view_w = self.viewport().width()
        if view_w <= 0:
            return

        min_card = self._target_min_card_w()
        spacing = self.SPACING

        # How many cards fit per row at the current density.
        #   2*spacing + N*card_w + (N-1)*spacing <= view_w
        #   N*(card_w + spacing) <= view_w - spacing
        cols = max(1, (view_w - spacing) // (min_card + spacing))

        usable = view_w - (cols + 1) * spacing
        card_w = max(min_card, usable // cols)
        if card_w > self.MAX_CARD_W:
            card_w = self.MAX_CARD_W
        card_h = card_w + LABEL_HEIGHT

        size_changed = (card_w != self._card_w or card_h != self._card_h)
        cols_changed = (cols != self._cols)
        self._card_w = card_w
        self._card_h = card_h
        self._cols = cols

        if self._model is not None:
            self._model.set_thumb_size(card_w - 12)

        # Center the row horizontally.
        used = cols * card_w + (cols - 1) * spacing
        self._outer = max(spacing, (view_w - used) // 2)

        # ── Virtual container height ──────────────────────────
        # The container must report the FULL height every row would
        # occupy if rendered, so the scroll bar accurately covers the
        # whole result set even though only a slice has live widgets.
        n_total = self._model.rowCount() if self._model is not None else 0
        rows = (n_total + cols - 1) // cols if n_total else 0
        total_h = spacing + rows * (card_h + spacing) if rows else 0
        self._container.setMinimumHeight(total_h)
        self._container.resize(view_w, max(total_h, self.viewport().height()))

        # If card size or column count changed, every currently-bound card
        # has a stale geometry — drop bindings so they're recomputed.
        # Cards go to the free pool, not into limbo: _update_visible_window
        # would otherwise be unable to find them and would hit POOL_CAP
        # without rendering anything.
        if size_changed or cols_changed:
            for card in self._row_to_card.values():
                card.hide()
                self._free_cards.append(card)
            self._row_to_card.clear()

        self._update_visible_window()

        if hasattr(self._container, "overlay"):
            self._container.overlay.setGeometry(
                0, 0, self._container.width(), self._container.height()
            )
            self._container.overlay.raise_()
        self.viewport().update()

    # ── Selection ───────────────────────────────────────────────────

    def _model_ids(self) -> list[int]:
        """All asset ids in model order. Replaces the old habit of walking
        self._cards — under virtualization the pool only contains the
        visible slice, so selection range and marquee logic now read row
        order straight from the model."""
        if self._model is None:
            return []
        return list(self._model._ids)

    def _on_card_clicked(self, asset_id: int, modifiers):
        ctrl = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        if ctrl:
            if asset_id in self._selected:
                self._selected.discard(asset_id)
            else:
                self._selected.add(asset_id)
        elif shift and self._last_click_aid is not None:
            # Range select from last clicked to this, using model row order
            ids = self._model_ids()
            try:
                a = ids.index(self._last_click_aid)
                b = ids.index(asset_id)
            except ValueError:
                self._selected = {asset_id}
            else:
                lo, hi = sorted((a, b))
                self._selected = set(ids[lo:hi + 1])
        else:
            self._selected = {asset_id}
        self._last_click_aid = asset_id
        self._sync_card_selection()
        self.selection_changed.emit()

    def _on_card_double_clicked(self, asset_id: int):
        # Emit a Qt-style doubleClicked signal with a model index for callers
        # that previously used the QListView signal.
        if self._model is None:
            return
        for row in range(self._model.rowCount()):
            if self._model.get_asset_id(row) == asset_id:
                self.doubleClicked.emit(self._model.index(row, 0))
                return

    def _on_card_context_menu(self, asset_id: int, global_pos: QPoint):
        # Make this card the only selection if it isn't already part of one.
        if asset_id not in self._selected:
            self._selected = {asset_id}
            self._last_click_aid = asset_id
            self._sync_card_selection()
            self.selection_changed.emit()
        self.customContextMenuRequested.emit(self.viewport().mapFromGlobal(global_pos))

    def _sync_card_selection(self):
        # Only push state into cards currently bound to a row — recycled
        # cards in the free pool have a stale asset_id from their last
        # binding and shouldn't reflect selection state.
        for card in self._row_to_card.values():
            card.set_selected(card.asset_id in self._selected)

    def selected_asset_ids(self) -> list[int]:
        # Order by model row so callers get deterministic ordering.
        return [aid for aid in self._model_ids() if aid in self._selected]

    def current_asset_id(self) -> int | None:
        return self._last_click_aid if self._last_click_aid in self._selected else (
            next(iter(self._selected), None)
        )

    def select_asset_ids(self, asset_ids: list[int]):
        # Intersect with model ids — caller may pass ids not currently in
        # the result set, which we don't want to "select" since they're
        # invisible.
        in_model = set(self._model_ids())
        self._selected = set(asset_ids) & in_model
        if self._selected:
            self._last_click_aid = next(iter(self._selected))
        self._sync_card_selection()
        self.selection_changed.emit()

    def clear_selection(self):
        if not self._selected:
            return
        self._selected.clear()
        self._sync_card_selection()
        self.selection_changed.emit()

    def _apply_marquee(self, rect: QRect, modifiers):
        """Update the live selection while a marquee is being dragged.

        With no modifier the marquee REPLACES selection. With Ctrl/Shift, the
        marquee adds to whatever was selected before the drag started — we
        snapshot the pre-marquee set on first contact so the user can see
        the diff in real time.
        """
        additive = bool(modifiers & (Qt.ControlModifier | Qt.ShiftModifier))
        if not additive:
            base: set[int] = set()
        else:
            base = self._pre_marquee_selection or set(self._selected)
            self._pre_marquee_selection = base

        # Compute hits from the row grid rather than from card widgets —
        # rows that scrolled out of the visible window have no card to
        # intersect against, so a card-based hit test would miss any row
        # the user dragged over but then scrolled past.
        hits: set[int] = set()
        if self._model is not None and self._cols > 0:
            n = self._model.rowCount()
            card_w, card_h, sp = self._card_w, self._card_h, self.SPACING
            row_h = card_h + sp
            ids = self._model._ids
            for row in range(n):
                grid_row, grid_col = divmod(row, self._cols)
                x = self._outer + grid_col * (card_w + sp)
                y = sp + grid_row * row_h
                if rect.intersects(QRect(x, y, card_w, card_h)):
                    hits.add(ids[row])
        new_sel = base | hits
        if new_sel != self._selected:
            self._selected = new_sel
            self._sync_card_selection()
            self.selection_changed.emit()
        # Drag finished? Clear the snapshot so the next click starts fresh.
        if rect.isNull():
            self._pre_marquee_selection = set()

    def select_all(self):
        self._selected = set(self._model_ids())
        self._sync_card_selection()
        self.selection_changed.emit()

    # ── Compatibility shims for the old QListView-based callers ─────

    def recenter(self):
        self._schedule_relayout()

    def scheduleDelayedItemsLayout(self):
        self._schedule_relayout()

    def setContextMenuPolicy(self, policy):
        # No-op — we always emit customContextMenuRequested on right-click.
        pass

    def setItemDelegate(self, delegate):
        # Kept for source compatibility; we paint inside AssetCard now.
        pass

    def selectionModel(self):
        return _SelectionAdapter(self)

    # ── Keyboard ────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Delete:
            self.delete_requested.emit()
            return
        if event.key() == Qt.Key_A and event.modifiers() & Qt.ControlModifier:
            self.select_all()
            return
        super().keyPressEvent(event)

    # ── Drag and drop ───────────────────────────────────────────────

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

    # ── Empty-state hint ────────────────────────────────────────────

    def paintEvent(self, event):
        super().paintEvent(event)
        # Empty-state hint shows only when the model has zero rows. With
        # virtualization, self._cards is the pool (typically non-empty
        # after first paint) so we can't gate on that any more.
        has_rows = self._model is not None and self._model.rowCount() > 0
        if has_rows or not self._show_empty_text:
            return
        p = QPainter(self.viewport())
        try:
            p.setRenderHint(QPainter.Antialiasing)
            f = QFont()
            f.setPixelSize(16)
            p.setFont(f)
            palette = self.palette()
            is_dark = palette.color(QPalette.Window).lightness() < 128
            p.setPen(QColor(100, 116, 139) if is_dark else QColor(71, 85, 105))
            p.drawText(
                self.viewport().rect(), Qt.AlignCenter,
                "Drag files here to get started\nor use File > Import",
            )
        finally:
            p.end()


class _SelectionAdapter:
    """Minimal QItemSelectionModel-compatible shim.

    The previous AssetListView inherited from QListView, so callers reached
    into `selectionModel().selectionChanged` to listen for selection updates.
    This adapter routes that signal through the custom view without forcing
    every caller to migrate.
    """

    def __init__(self, view: AssetListView):
        self._view = view
        self.selectionChanged = view.selection_changed


# Backwards-compat alias — old code imported ThumbnailDelegate to register it
# with the QListView. The custom AssetCard renders its own pixels now, so the
# delegate is a no-op placeholder.
class ThumbnailDelegate:
    def __init__(self, parent=None):
        pass

    def set_cell_size(self, w: int, h: int):
        pass

    def set_thumb_size(self, size: int):
        pass
