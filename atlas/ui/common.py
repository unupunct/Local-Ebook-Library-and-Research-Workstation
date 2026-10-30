"""Small shared UI helpers."""
from __future__ import annotations

import hashlib
import os
from collections import OrderedDict

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)


def human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def human_duration(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h else f"{m} min"


def label(text: str = "", style: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if style:
        lb.setObjectName(style)
    lb.setWordWrap(wrap)
    return lb


def card(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    if title:
        lay.addWidget(label(title, "h2"))
    return f, lay


def info(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def warn(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def confirm(parent: QWidget | None, title: str, text: str, yes: str = "Yes") -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, parent)
    y = box.addButton(yes, QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is y


def button(text: str, slot=None, primary: bool = False, tip: str = "", danger: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("primary")
    if danger:
        b.setObjectName("danger")
    if tip:
        b.setToolTip(tip)
        b.setAccessibleName(text)
    if slot:
        b.clicked.connect(lambda _=False: slot())
    return b


def hbox(*widgets, spacing: int = 6, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        else:
            lay.addWidget(w)
    return lay


STATUS_TEXT = {
    "pending": "Waiting to be indexed", "indexed": "Indexed", "partial": "Partly indexed",
    "failed": "Failed", "encrypted": "Encrypted / protected", "unsupported": "Unsupported format",
    "ocr_needed": "Needs OCR",
}


class CoverCache:
    """Loads cover thumbnails from the cache folder; draws a consistent fallback cover."""

    def __init__(self, size: tuple[int, int] = (120, 180), capacity: int = 600) -> None:
        self.size = size
        self.cap = capacity
        self._c: OrderedDict[tuple, QPixmap] = OrderedDict()

    def get(self, cover_path: str, title: str, authors: str, size: tuple[int, int] | None = None, badge: str = "") -> QPixmap:
        sz = size or self.size
        key = (cover_path or f"fb:{title}|{authors}", sz, badge)
        pm = self._c.get(key)
        if pm is not None:
            self._c.move_to_end(key)
            return pm
        pm = None
        if cover_path and os.path.isfile(cover_path):
            img = QImage(cover_path)
            if not img.isNull():
                pm = QPixmap.fromImage(img.scaled(sz[0], sz[1], Qt.KeepAspectRatio, Qt.SmoothTransformation))
        if pm is None:
            pm = fallback_cover(title, authors, sz)
        if badge:
            pm = add_badge(pm, badge)
        self._c[key] = pm
        if len(self._c) > self.cap:
            self._c.popitem(last=False)
        return pm


BADGES = {"offline": ("#8b93a1", "Source offline"), "problem": ("#d9534f", "Could not be indexed"), "attention": ("#e0a030", "Waiting, partial or needs OCR")}


def badge_for(available: int, index_status: str) -> str:
    if not available:
        return "offline"
    if index_status in ("failed", "encrypted", "unsupported"):
        return "problem"
    if index_status in ("pending", "ocr_needed", "partial"):
        return "attention"
    return ""


def add_badge(pm: QPixmap, kind: str) -> QPixmap:
    """Small coloured dot in the top-right corner of a cover (status at a glance)."""
    out = QPixmap(pm)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    d = max(9, out.width() // 6)
    p.setBrush(QColor(BADGES[kind][0]))
    p.setPen(QColor("white"))
    p.drawEllipse(out.width() - d - 3, 3, d, d)
    p.end()
    return out


def fallback_cover(title: str, authors: str, size: tuple[int, int]) -> QPixmap:
    h = int(hashlib.md5((title + authors).encode("utf-8", "ignore")).hexdigest()[:6], 16)
    base = QColor.fromHsv(h % 360, 90, 110)
    w, ht = size
    pm = QPixmap(w, ht)
    pm.fill(base)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, w, max(4, ht // 40), base.lighter(150))
    p.setPen(QColor("#f1f1f1"))
    f = QFont("Georgia")
    f.setPixelSize(max(9, w // 9))
    f.setBold(True)
    p.setFont(f)
    margin = w * 0.1
    p.drawText(QRectF(margin, ht * 0.12, w - 2 * margin, ht * 0.6), Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, title[:90])
    f2 = QFont("Segoe UI")
    f2.setPixelSize(max(8, w // 12))
    p.setFont(f2)
    p.setPen(QColor("#dddddd"))
    p.drawText(QRectF(margin, ht * 0.74, w - 2 * margin, ht * 0.22), Qt.AlignHCenter | Qt.AlignBottom | Qt.TextWordWrap, authors[:60])
    p.end()
    return pm
