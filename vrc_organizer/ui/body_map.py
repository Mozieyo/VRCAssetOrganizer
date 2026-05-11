from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF, QPointF, QSize
from PySide6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QFont,
    QPolygonF, QFontMetrics, QPalette,
)
from PySide6.QtWidgets import QWidget, QSizePolicy

# Body segment definitions with label, color, and tag mapping
SEGMENTS = [
    ("hair", "Hair", QColor(239, 68, 68)),       # red
    ("head", "Head", QColor(251, 191, 36)),       # amber
    ("body", "Body", QColor(59, 130, 246)),       # blue
    ("left_hand", "Hands", QColor(168, 85, 247)), # purple
    ("right_hand", "Hands", QColor(168, 85, 247)),
    ("left_foot", "Feet", QColor(34, 197, 94)),   # green
    ("right_foot", "Feet", QColor(34, 197, 94)),
    ("ears", "Ears", QColor(236, 72, 153)),       # pink
    ("tail", "Tail", QColor(249, 115, 22)),       # orange
    ("accessories", "Accs", QColor(99, 102, 241)),# indigo
]


class BodyMapWidget(QWidget):
    """Interactive avatar silhouette for visual tag filtering."""
    segment_toggled = Signal(str, bool)  # tag_name, checked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 240)
        self.setMaximumHeight(330)
        self.setMouseTracking(True)
        sp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        sp.setHeightForWidth(True)
        self.setSizePolicy(sp)

        self._paths: dict[str, QPainterPath] = {}
        self._labels: dict[str, tuple[QRectF, str]] = {}
        self._active: set[str] = set()
        self._hovered: str = ""
        self._segment_key_to_tag: dict[str, str] = {
            s[0]: s[1] for s in SEGMENTS
        }

    def sizeHint(self):
        return QSize(180, 270)

    def heightForWidth(self, width: int) -> int:
        return int(width * 3 / 2)

    # ── Path Building ─────────────────────────────────────

    def _build_paths(self):
        self._paths.clear()
        self._labels.clear()
        w = self.width()
        h = self.height()

        cx = w / 2
        head_top = h * 0.08
        head_bottom = h * 0.24
        neck_y = h * 0.28
        body_bottom = h * 0.52
        hip_y = h * 0.55
        foot_y = h * 0.95
        hand_y = h * 0.45
        shoulder_x_outer = w * 0.12
        shoulder_x_inner = w * 0.35
        arm_outer = w * 0.08

        # Head
        head = QPainterPath()
        head.addEllipse(QPointF(cx, (head_top + head_bottom) / 2),
                        w * 0.14, (head_bottom - head_top) / 2)
        self._paths["head"] = head
        self._labels["head"] = (head.boundingRect(), "Head")

        # Hair (arc above head)
        hair = QPainterPath()
        hair_rect = QRectF(cx - w * 0.18, head_top - h * 0.02,
                           w * 0.36, (head_bottom - head_top) * 0.8)
        hair.arcMoveTo(hair_rect, 180)
        hair.arcTo(hair_rect, 180, 180)
        hair.closeSubpath()
        self._paths["hair"] = hair
        self._labels["hair"] = (hair.boundingRect(), "Hair")

        # Ears
        ear_w = w * 0.06
        ear_h = h * 0.05
        left_ear = QPainterPath()
        left_ear.addEllipse(QPointF(cx - w * 0.15, (head_top + head_bottom) / 2),
                            ear_w, ear_h)
        self._paths["ears"] = left_ear
        self._labels["ears"] = (left_ear.boundingRect(), "Ears")

        # Body / torso
        body = QPainterPath()
        body.moveTo(cx - w * 0.12, neck_y)
        body.lineTo(cx + w * 0.12, neck_y)
        body.lineTo(cx + w * 0.16, body_bottom)
        body.lineTo(cx - w * 0.16, body_bottom)
        body.closeSubpath()
        self._paths["body"] = body
        self._labels["body"] = (body.boundingRect(), "Body")

        # Left arm + hand
        left_arm = QPainterPath()
        left_arm.moveTo(cx - w * 0.12, neck_y + h * 0.02)
        left_arm.quadTo(cx - w * 0.35, neck_y + h * 0.04,
                        cx - w * 0.40, hand_y)
        left_arm.lineTo(cx - w * 0.30, hand_y)
        left_arm.quadTo(cx - w * 0.22, neck_y + h * 0.06,
                        cx - w * 0.08, neck_y + h * 0.04)
        left_arm.closeSubpath()
        self._paths["left_hand"] = left_arm
        self._labels["left_hand"] = (left_arm.boundingRect(), "Hands")

        # Right arm + hand
        right_arm = QPainterPath()
        right_arm.moveTo(cx + w * 0.12, neck_y + h * 0.02)
        right_arm.quadTo(cx + w * 0.35, neck_y + h * 0.04,
                         cx + w * 0.40, hand_y)
        right_arm.lineTo(cx + w * 0.30, hand_y)
        right_arm.quadTo(cx + w * 0.22, neck_y + h * 0.06,
                         cx + w * 0.08, neck_y + h * 0.04)
        right_arm.closeSubpath()
        self._paths["right_hand"] = right_arm
        self._labels["right_hand"] = (right_arm.boundingRect(), "Hands")

        # Left leg + foot
        left_leg = QPainterPath()
        left_leg.moveTo(cx - w * 0.10, hip_y)
        left_leg.quadTo(cx - w * 0.18, (hip_y + foot_y) / 2,
                        cx - w * 0.20, foot_y)
        left_leg.lineTo(cx - w * 0.04, foot_y)
        left_leg.quadTo(cx - w * 0.04, (hip_y + foot_y) / 2,
                        cx - w * 0.02, hip_y)
        left_leg.closeSubpath()
        self._paths["left_foot"] = left_leg
        self._labels["left_foot"] = (left_leg.boundingRect(), "Feet")

        # Right leg + foot
        right_leg = QPainterPath()
        right_leg.moveTo(cx + w * 0.02, hip_y)
        right_leg.quadTo(cx + w * 0.04, (hip_y + foot_y) / 2,
                         cx + w * 0.04, foot_y)
        right_leg.lineTo(cx + w * 0.20, foot_y)
        right_leg.quadTo(cx + w * 0.18, (hip_y + foot_y) / 2,
                         cx + w * 0.10, hip_y)
        right_leg.closeSubpath()
        self._paths["right_foot"] = right_leg
        self._labels["right_foot"] = (right_leg.boundingRect(), "Feet")

        # Tail (back)
        tail = QPainterPath()
        tail.moveTo(cx + w * 0.08, hip_y - h * 0.02)
        tail.quadTo(cx + w * 0.25, hip_y + h * 0.05,
                    cx + w * 0.22, hip_y + h * 0.15)
        tail.quadTo(cx + w * 0.18, hip_y + h * 0.05,
                    cx + w * 0.06, hip_y - h * 0.01)
        tail.closeSubpath()
        self._paths["tail"] = tail
        self._labels["tail"] = (tail.boundingRect(), "Tail")

        # Accessories (floating around)
        acc = QPainterPath()
        acc.addEllipse(QPointF(cx + w * 0.22, head_top + h * 0.02),
                       w * 0.05, h * 0.03)
        acc.addEllipse(QPointF(cx - w * 0.20, head_top + h * 0.05),
                       w * 0.04, h * 0.025)
        self._paths["accessories"] = acc
        self._labels["accessories"] = (acc.boundingRect(), "Accs")

    # ── Events ────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._build_paths()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bg = self.palette().color(QPalette.Window)
        painter.fillRect(self.rect(), bg)

        for seg_key, seg_label, seg_color in SEGMENTS:
            path = self._paths.get(seg_key)
            if path is None:
                continue

            tag_name = self._segment_key_to_tag[seg_key]
            is_active = tag_name in self._active
            is_hovered = self._hovered == seg_key

            if is_active:
                painter.setBrush(QBrush(seg_color.lighter(150)))
                painter.setPen(QPen(seg_color, 2))
            elif is_hovered:
                painter.setBrush(QBrush(seg_color.lighter(180)))
                painter.setPen(QPen(seg_color, 2))
            else:
                painter.setBrush(QBrush(QColor(226, 232, 240)))
                painter.setPen(QPen(QColor(203, 213, 225), 1))

            painter.drawPath(path)

        # Draw labels for active segments
        painter.setFont(QFont("Segoe UI", 8))
        key_to_color = {s[0]: s[2] for s in SEGMENTS}
        for seg_key, (rect, label) in self._labels.items():
            tag_name = self._segment_key_to_tag.get(seg_key, "")
            if tag_name in self._active:
                painter.setPen(Qt.white)
                painter.setBrush(QBrush(key_to_color[seg_key]))
                lr = QRectF(rect.center().x() - 20, rect.center().y() - 8, 40, 16)
                painter.drawRoundedRect(lr, 4, 4)
                painter.drawText(lr, Qt.AlignCenter, label)

    def mouseMoveEvent(self, event):
        pos = event.position()
        old = self._hovered
        self._hovered = ""
        for seg_key, path in self._paths.items():
            if path.contains(pos):
                self._hovered = seg_key
                break
        if old != self._hovered:
            self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        for seg_key, path in self._paths.items():
            if path.contains(pos):
                tag_name = self._segment_key_to_tag[seg_key]
                if tag_name in self._active:
                    self._active.discard(tag_name)
                    self.segment_toggled.emit(tag_name, False)
                else:
                    self._active.add(tag_name)
                    self.segment_toggled.emit(tag_name, True)
                self.update()
                return

    def leaveEvent(self, event):
        self._hovered = ""
        self.update()

    def clear_active(self):
        self._active.clear()
        self.update()

    def active_tags(self) -> list[str]:
        return list(self._active)
