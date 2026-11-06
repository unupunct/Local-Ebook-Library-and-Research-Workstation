"""Micro-benchmarks for None-refcount leaks in PySide6 override patterns (meaningful on Python < 3.12 only)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QProgressBar, QTextBrowser, QToolButton, QWidget

app = QApplication([])
RC = lambda: sys.getrefcount(None)


def measure(label, fn, n=50):
    fn()
    a = RC()
    for _ in range(n):
        fn()
    print(f"{label:58s} delta/iter = {(RC() - a) / n:+.2f}", flush=True)


def shown(w):
    w.resize(300, 200); w.show(); app.processEvents(); return w


plain = shown(QTextBrowser())
measure("QTextBrowser repaint", lambda: plain.viewport().repaint())


class Sub(QTextBrowser):
    def resizeEvent(self, e):
        super().resizeEvent(e)

    def wheelEvent(self, e):
        super().wheelEvent(e)


sub = shown(Sub())
measure("QTextBrowser subclass (resizeEvent, wheelEvent)", lambda: sub.viewport().repaint())

assigned = shown(QTextBrowser())
orig = assigned.mousePressEvent
assigned.mousePressEvent = lambda e: orig(e)
measure("QTextBrowser with instance-assigned mousePressEvent", lambda: assigned.viewport().repaint())


class Area(QAbstractScrollArea):
    def paintEvent(self, e):
        p = QPainter(self.viewport()); p.fillRect(self.viewport().rect(), Qt.red); p.end()


area = shown(Area())
measure("QAbstractScrollArea with Python paintEvent", lambda: area.viewport().repaint())


class Plain(QAbstractScrollArea):
    pass


pl = shown(Plain())
measure("QAbstractScrollArea subclass, no overrides", lambda: pl.viewport().repaint())

w = shown(QWidget())
sc = QShortcut(QKeySequence("Ctrl+T"), w, activated=lambda: None)
measure("QWidget with QShortcut(activated=lambda)", lambda: (w.update(), app.processEvents()))
w2 = shown(QWidget())
t = QTimer(w2, interval=5000)
t.timeout.connect(lambda: None)
t.start()
measure("QWidget with running QTimer(parent)", lambda: (w2.update(), app.processEvents()))
b = shown(QToolButton())
b.setCheckable(True); b.toggled.connect(lambda on: None)
measure("QToolButton with toggled lambda", lambda: (b.update(), app.processEvents()))
pb = shown(QProgressBar())
measure("QProgressBar setValue", lambda: (pb.setValue(pb.value() % 90 + 1), app.processEvents()))
measure("idle processEvents", lambda: app.processEvents())
