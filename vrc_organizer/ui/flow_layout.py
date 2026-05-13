from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QPoint, QSize
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget, QWidgetItem as _QtWidgetItem


class _ItemWrapper:
    """Wrapper that satisfies QLayout's itemAt/takeAt interface.
    We store QWidgetItem objects but use a wrapper because PyInstaller-packaged
    PySide6 sometimes has issues with QWidgetItem type checks in QLayout internals."""

    def __init__(self, widget: QWidget):
        self._qt_item = _QtWidgetItem(widget)
        self._widget = widget

    def widget(self) -> QWidget:
        return self._widget

    def sizeHint(self) -> QSize:
        return self._qt_item.sizeHint()

    def minimumSize(self) -> QSize:
        return self._qt_item.minimumSize()

    def setGeometry(self, rect: QRect):
        self._qt_item.setGeometry(rect)

    def isEmpty(self) -> bool:
        return False

    def __layoutitem__(self):
        return self._qt_item


class FlowLayout(QLayout):
    """Layout that arranges child widgets in a wrapping flow — fills width,
    overflows to the next row. Similar to CSS flex-wrap."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4):
        super().__init__(parent)
        self._items: list[_ItemWrapper] = []
        self._h_spacing = spacing
        self._v_spacing = spacing
        self._in_layout = False
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item):
        # Convert QWidgetItem to our wrapper if needed
        if isinstance(item, _QtWidgetItem):
            w = item.widget()
            if w is not None:
                self._items.append(_ItemWrapper(w))
        elif isinstance(item, _ItemWrapper):
            self._items.append(item)
        self.invalidate()

    def addWidget(self, w: QWidget):
        self._items.append(_ItemWrapper(w))
        if self.parentWidget():
            w.setParent(self.parentWidget())
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            wrapper = self._items[index]
            # Return the underlying QLayoutItem so PySide6 is happy
            return wrapper._qt_item
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            wrapper = self._items.pop(index)
            self.invalidate()
            return wrapper._qt_item
        return None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        if self._in_layout:
            return
        self._in_layout = True
        try:
            self._do_layout(rect, test_only=False)
        finally:
            self._in_layout = False

    def sizeHint(self) -> QSize:
        # Calculate total layout height for the available width.
        # minimumSize() returns only the max single-item size, which
        # causes QScrollArea containers to collapse to one row.
        w = 200
        parent = self.parentWidget()
        if parent:
            margins = parent.contentsMargins()
            avail = parent.width() - margins.left() - margins.right()
            if avail > 0:
                w = avail
        h = self.heightForWidth(w)
        return QSize(w, max(h, self.minimumSize().height()))

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = r.x()
        y = r.y()
        line_h = 0

        for item in self._items:
            w = item.widget()
            if w is not None and not w.isVisible():
                continue
            size_hint = item.sizeHint()
            next_x = x + size_hint.width() + self._h_spacing
            if next_x - self._h_spacing > r.right() and line_h > 0:
                x = r.x()
                y = y + line_h + self._v_spacing
                next_x = x + size_hint.width() + self._h_spacing
                line_h = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), size_hint))
            x = next_x
            line_h = max(line_h, size_hint.height())

        return y + line_h - rect.y() + m.bottom()
