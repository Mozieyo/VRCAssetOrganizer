from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    Qt, QAbstractListModel, QModelIndex, Signal, QSize, QRect, QPoint, QTimer,
    QItemSelectionModel, QEvent,
)
from PySide6.QtGui import (
    QPainter, QPixmap, QColor, QFont, QPen, QBrush, QFontMetrics, QPalette,
    QMouseEvent, QKeyEvent,
)
from PySide6.QtWidgets import (
    QWidget, QScrollArea, QFrame, QVBoxLayout, QApplication, QSizePolicy,
)

from vrc_organizer.database.queries import Queries

PAGE_SIZE = 100
THUMB_SIZE = 192
MAX_CACHED_THUMBS = 200
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
        self._asset_cache = {}
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
            from vrc_organizer.romaji import has_japanese, to_romaji
            from PySide6.QtCore import QSettings
            show_romaji = bool(QSettings().value("show_romaji", True, type=bool))
            jp = show_romaji and has_japanese(self._filename)

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model: AssetListModel | None = None
        self._cards: list[AssetCard] = []
        self._selected: set[int] = set()
        self._pre_marquee_selection: set[int] = set()
        self._last_click_aid: int | None = None
        self._density = 5
        self._card_w = self.MIN_CARD_W
        self._card_h = self.MIN_CARD_W + LABEL_HEIGHT
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

    def _on_data_changed(self, *args):
        self._refresh_card_data()

    def _ensure_all_rows_loaded(self):
        if self._model is None:
            return
        # Hard-cap to avoid runaway loops if total is huge.
        guard = 0
        while self._model.canFetchMore(QModelIndex()) and guard < 50:
            self._model.fetchMore(QModelIndex())
            guard += 1

    def _rebuild(self):
        # Guard against re-entrancy: fetchMore inside _ensure_all_rows_loaded
        # fires rowsInserted, which would otherwise schedule another rebuild
        # mid-flight.
        if getattr(self, "_rebuilding", False):
            return
        self._rebuilding = True
        try:
            for c in self._cards:
                c.setParent(None)
                c.deleteLater()
            self._cards.clear()
            if self._model is None:
                self._schedule_relayout()
                return
            self._ensure_all_rows_loaded()
            for row in range(self._model.rowCount()):
                aid = self._model.get_asset_id(row)
                card = AssetCard(aid, self._container)
                card.clicked.connect(self._on_card_clicked)
                card.double_clicked.connect(self._on_card_double_clicked)
                card.context_menu.connect(self._on_card_context_menu)
                card.show()
                self._cards.append(card)
            self._refresh_card_data()
            self._schedule_relayout()
        finally:
            self._rebuilding = False

    def _refresh_card_data(self):
        if self._model is None:
            return
        for i, card in enumerate(self._cards):
            aid = card.asset_id
            idx = self._model.index(i, 0) if i < self._model.rowCount() else None
            filename = self._model._get_filename(aid)
            pix = self._model._get_thumb(aid)
            tooltip = self._model._get_tooltip(aid)
            card.set_data(filename, pix, tooltip)

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

        # Available row width = view_w - 2*outer_margin. Each card consumes
        # card_w + spacing in horizontal flow. Outer margin == spacing for
        # symmetry, so a row fits N cards when:
        #   2*spacing + N*card_w + (N-1)*spacing <= view_w
        #   N*(card_w + spacing) <= view_w - spacing
        cols = max(1, (view_w - spacing) // (min_card + spacing))

        # Grow each card to fill the row exactly.
        usable = view_w - (cols + 1) * spacing
        card_w = max(min_card, usable // cols)
        if card_w > self.MAX_CARD_W:
            card_w = self.MAX_CARD_W
        card_h = card_w + LABEL_HEIGHT  # square thumb area + label band

        self._card_w = card_w
        self._card_h = card_h

        # Tell the model the new pixmap target so cached thumbnails render at
        # the right resolution. Only triggers a cache clear when the size
        # actually changes (model handles that).
        if self._model is not None:
            self._model.set_thumb_size(card_w - 12)

        # Center the row: actual content width = cols*card_w + (cols-1)*spacing.
        # Outer left/right margin is half the remaining space, but never less
        # than the configured spacing.
        used = cols * card_w + (cols - 1) * spacing
        outer = max(spacing, (view_w - used) // 2)

        # Place each card.
        n = len(self._cards)
        for i, card in enumerate(self._cards):
            r = i // cols
            c = i % cols
            x = outer + c * (card_w + spacing)
            y = spacing + r * (card_h + spacing)
            card.setGeometry(x, y, card_w, card_h)

        # Resize container so the scroll area knows how tall content is.
        rows = (n + cols - 1) // cols if n else 0
        total_h = spacing + rows * (card_h + spacing) if rows else 0
        self._container.setMinimumHeight(max(total_h, 0))
        self._container.resize(view_w, max(total_h, self.viewport().height()))
        # Re-raise the marquee overlay above the cards every layout pass,
        # otherwise a freshly-placed card draws on top of it.
        if hasattr(self._container, "overlay"):
            self._container.overlay.setGeometry(
                0, 0, self._container.width(), self._container.height()
            )
            self._container.overlay.raise_()
        self.viewport().update()

    # ── Selection ───────────────────────────────────────────────────

    def _on_card_clicked(self, asset_id: int, modifiers):
        ctrl = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)
        if ctrl:
            if asset_id in self._selected:
                self._selected.discard(asset_id)
            else:
                self._selected.add(asset_id)
        elif shift and self._last_click_aid is not None:
            # Range select from last to this
            ids = [c.asset_id for c in self._cards]
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
        for card in self._cards:
            card.set_selected(card.asset_id in self._selected)

    def selected_asset_ids(self) -> list[int]:
        return [c.asset_id for c in self._cards if c.asset_id in self._selected]

    def current_asset_id(self) -> int | None:
        return self._last_click_aid if self._last_click_aid in self._selected else (
            next(iter(self._selected), None)
        )

    def select_asset_ids(self, asset_ids: list[int]):
        target = set(asset_ids)
        self._selected = {c.asset_id for c in self._cards if c.asset_id in target}
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

        hits: set[int] = set()
        for card in self._cards:
            if rect.intersects(card.geometry()):
                hits.add(card.asset_id)
        new_sel = base | hits
        if new_sel != self._selected:
            self._selected = new_sel
            self._sync_card_selection()
            self.selection_changed.emit()
        # Drag finished? Clear the snapshot so the next click starts fresh.
        if rect.isNull():
            self._pre_marquee_selection = set()

    def select_all(self):
        self._selected = {c.asset_id for c in self._cards}
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
        if self._cards or not self._show_empty_text:
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
