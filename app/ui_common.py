"""Theme, sidebar painter and small reusable widgets (same look as the user's other tools)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QSlider, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

THEME = {
    'bg': '#0e1116', 'side': '#090c10', 'nav': '#0a0e13', 'panel': '#11161c', 'card': '#151a21', 'input': '#151a21',
    'button': '#171d26', 'button_hover': '#222b37', 'border': '#252c35', 'border_strong': '#2a3340', 'text': '#e8ecf2',
    'text_strong': '#ffffff', 'subtext': '#8994a4', 'muted': '#7f8a99', 'nav_text': '#b9c2cf', 'hover': '#161e28',
    'header_bg': '#191f27', 'header_text': '#aeb7c4', 'grid': '#222933', 'alt_row': '#151a21', 'selection': '#263342',
    'selection_text': '#ffffff', 'chart_bg': '#0d1218', 'chart_grid': '#26303b', 'scroll': '#3a4654',
    'accent': '#4dc9ff', 'ok': '#45d483', 'warn': '#f0ad4e', 'err': '#ff6577', 'pink': '#e51e63',
}


def TC(key):
    return QColor(THEME.get(key, '#ff00ff'))


def stylesheet(font_family="Microsoft JhengHei UI"):
    t = THEME
    return f'''
    QMainWindow,QWidget{{background:{t['bg']};color:{t['text']};font-family:"{font_family}","Microsoft JhengHei";font-size:10pt}}
    QFrame#side{{background:{t['side']};border-right:1px solid {t['border']}}} QLabel#brand{{font-weight:900;letter-spacing:1px;font-size:12pt}}
    QLabel#title{{font-weight:850;font-size:13pt}} QLabel#sub{{color:{t['subtext']}}} QLabel#section_title{{font-weight:800}}
    QPushButton{{background:{t['button']};border:1px solid {t['border_strong']};border-radius:8px;padding:5px 10px;color:{t['text']}}}
    QPushButton:hover{{background:{t['button_hover']}}} QPushButton:disabled{{color:{t['muted']}}}
    QPushButton:checked{{background:#203246;border:1px solid #3d78a8;color:#ffffff}}
    QPushButton#start{{background:{t['pink']};border:0;font-weight:900;min-height:32px;color:#ffffff}}
    QPushButton#start:disabled{{background:#4a2433;color:#b98a99}}
    QPushButton#blue{{background:#1f5f99;border:0;font-weight:800;color:#ffffff}}
    QListWidget#workspace_nav{{background:{t['nav']};border:0;border-radius:9px;padding:2px 1px;outline:0;}}
    QListWidget#workspace_nav::item{{border:0;background:transparent;}}
    QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox{{background:{t['input']};border:1px solid {t['border_strong']};border-radius:7px;padding:3px 6px;color:{t['text_strong']}}}
    QComboBox QAbstractItemView{{background:{t['panel']};color:{t['text']};selection-background-color:{t['selection']};selection-color:#fff}}
    QMenu{{background:{t['panel']};color:{t['text']};border:1px solid {t['border']}}} QMenu::item:selected{{background:{t['selection']};color:#fff}}
    QListWidget{{background:transparent;border:0;outline:0}} QListWidget::item{{padding:0px 4px;border-radius:7px;color:{t['nav_text']}}}
    QListWidget::item:selected{{background:{t['selection']};color:#fff}} QListWidget::item:hover{{background:{t['hover']}}}
    QTableWidget,QTableView{{background:{t['panel']};border:1px solid {t['border']};gridline-color:{t['grid']};border-radius:8px;alternate-background-color:{t['alt_row']};selection-background-color:{t['selection']};outline:0}}
    QHeaderView::section{{background:{t['header_bg']};color:{t['header_text']};padding:5px;border:0;font-weight:700}}
    QTableCornerButton::section{{background:{t['header_bg']};border:0}}
    QFrame#card{{background:{t['card']};border:1px solid {t['border']};border-radius:11px}}
    QFrame#hero{{background:{t['card']};border:1px solid {t['border_strong']};border-radius:13px}}
    QFrame#panel{{background:{t['panel']};border:1px solid {t['border']};border-radius:9px}}
    QLabel#card_title{{background:transparent;color:{t['subtext']}}} QLabel#card_value{{background:transparent;font-weight:900;font-size:15pt;color:#fff}}
    QCheckBox,QRadioButton{{color:{t['text']};background:transparent}} QToolTip{{background:{t['panel']};color:{t['text']};border:1px solid {t['border']}}}
    QTabWidget::pane{{border:1px solid {t['border']};border-radius:8px;top:-1px}}
    QTabBar::tab{{background:{t['panel']};color:{t['subtext']};padding:7px 16px;border:1px solid {t['border']};border-bottom:0;border-top-left-radius:8px;border-top-right-radius:8px;margin-right:2px}}
    QTabBar::tab:selected{{background:#202a35;color:#fff;font-weight:800}}
    QProgressBar{{background:{t['input']};border:1px solid {t['border']};border-radius:6px;text-align:center;color:#fff;height:16px}}
    QProgressBar::chunk{{background:#2b8ad8;border-radius:5px}}
    QSlider::groove:horizontal{{height:4px;background:{t['grid']};border-radius:2px}}
    QSlider::sub-page:horizontal{{background:#2b8ad8;border-radius:2px}}
    QSlider::handle:horizontal{{background:#dfe8f3;width:12px;height:12px;margin:-5px 0;border-radius:6px}}
    QScrollBar:vertical{{background:transparent;width:8px}} QScrollBar::handle:vertical{{background:{t['scroll']};border-radius:4px;min-height:28px}}
    QScrollBar::add-line,QScrollBar::sub-line{{height:0;width:0}}
    QScrollBar:horizontal{{background:transparent;height:8px}} QScrollBar::handle:horizontal{{background:{t['scroll']};border-radius:4px;min-width:28px}}
    QScrollArea{{border:0}}
    QGroupBox{{border:1px solid {t['border']};border-radius:9px;margin-top:14px;padding:8px 6px 6px;font-weight:800}}
    QGroupBox::title{{subcontrol-origin:margin;left:10px;padding:0 4px;color:{t['accent']}}}
    QStatusBar{{color:{t['subtext']}}} QLabel{{background:transparent}}
    QDoubleSpinBox#num::up-button,QDoubleSpinBox#num::down-button{{width:0;border:0}} QDoubleSpinBox#num{{padding:2px 6px}}
    '''


class NavDelegate(QStyledItemDelegate):
    """Sidebar painter: coloured section titles vs. clickable entries with an icon column."""

    def sizeHint(self, option, index):
        kind = index.data(Qt.UserRole + 2)
        h = QFontMetrics(option.font).height()
        return QSize(option.rect.width(), (h + 10) if kind == 'header' else (h + 13))

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        kind = index.data(Qt.UserRole + 2)
        text = str(index.data(Qt.DisplayRole) or '')
        color = QColor(str(index.data(Qt.UserRole + 1) or '#4dc9ff'))
        r = option.rect
        if kind == 'header':
            f = QFont(option.font)
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.82))
            f.setLetterSpacing(QFont.PercentageSpacing, 108)
            painter.setFont(f)
            fm = QFontMetrics(f)
            y = r.bottom() - 6
            painter.setPen(color)
            painter.drawText(QRectF(r.left() + 10, r.top(), r.width() - 14, r.height() - 4), Qt.AlignLeft | Qt.AlignBottom, text)
            x0 = r.left() + 14 + fm.horizontalAdvance(text)
            if x0 < r.right() - 8:
                c = QColor(color)
                c.setAlpha(70)
                painter.setPen(QPen(c, 1))
                painter.drawLine(int(x0), int(y - fm.height() / 2 + 3), int(r.right() - 8), int(y - fm.height() / 2 + 3))
        else:
            selected = bool(option.state & QStyle.State_Selected)
            hover = bool(option.state & QStyle.State_MouseOver)
            box = QRectF(r.left() + 4, r.top() + 2, r.width() - 8, r.height() - 4)
            if selected:
                bg = QColor(color)
                bg.setAlpha(38)
                painter.setPen(Qt.NoPen)
                painter.setBrush(bg)
                painter.drawRoundedRect(box, 7, 7)
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(box.left(), box.top() + 5, 3.5, box.height() - 10), 2, 2)
            elif hover:
                painter.setPen(Qt.NoPen)
                painter.setBrush(TC('hover'))
                painter.drawRoundedRect(box, 7, 7)
            f = QFont(option.font)
            f.setBold(selected)
            painter.setFont(f)
            icon = str(index.data(Qt.UserRole + 3) or '')
            painter.setPen(color if selected else TC('muted'))
            painter.drawText(QRectF(box.left() + 10, box.top(), 22, box.height()), Qt.AlignCenter, icon)
            painter.setPen(TC('text_strong') if selected else (TC('text') if hover else TC('nav_text')))
            badge = index.data(Qt.UserRole + 4)
            painter.drawText(QRectF(box.left() + 38, box.top(), box.width() - 42, box.height()), Qt.AlignLeft | Qt.AlignVCenter, text)
            if badge:
                bf = QFont(option.font)
                bf.setPointSizeF(bf.pointSizeF() * 0.8)
                bf.setBold(True)
                painter.setFont(bf)
                fm = QFontMetrics(bf)
                bw = fm.horizontalAdvance(str(badge)) + 12
                br = QRectF(box.right() - bw - 6, box.center().y() - 9, bw, 18)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor('#e51e63'))
                painter.drawRoundedRect(br, 9, 9)
                painter.setPen(QColor('#ffffff'))
                painter.drawText(br, Qt.AlignCenter, str(badge))
        painter.restore()


class Sparkline(QWidget):
    """Tiny line chart; values are 0..vmax (vmax None = auto)."""

    def __init__(self, color='#4dc9ff', vmax=100.0, parent=None, fill=True):
        super().__init__(parent)
        self.color, self.vmax, self.values, self.fill = QColor(color), vmax, [], fill
        self.setMinimumHeight(24)

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        vals = self.values[-120:]
        if len(vals) < 2:
            return
        vmax = self.vmax or max(1e-6, max(vals) * 1.15)
        n = len(vals)
        pts = [QPointF(r.left() + r.width() * i / (n - 1), r.bottom() - r.height() * min(1.0, max(0.0, v / vmax)))
               for i, v in enumerate(vals)]
        path = QPainterPath(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        if self.fill:
            fp = QPainterPath(path)
            fp.lineTo(r.right(), r.bottom())
            fp.lineTo(r.left(), r.bottom())
            fp.closeSubpath()
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            c1 = QColor(self.color); c1.setAlpha(90)
            c2 = QColor(self.color); c2.setAlpha(8)
            g.setColorAt(0, c1); g.setColorAt(1, c2)
            p.fillPath(fp, g)
        p.setPen(QPen(self.color, 1.6))
        p.drawPath(path)


class ParamRow(QWidget):
    """Label + slider + spin box bound to one float value (display = value * scale)."""
    changed = Signal(float)

    def __init__(self, label, lo, hi, step, decimals=2, scale=1.0, tip='', parent=None):
        super().__init__(parent)
        self.scale, self.step = scale, step
        l = QHBoxLayout(self)
        l.setContentsMargins(0, 1, 0, 1)
        l.setSpacing(6)
        self.lab = QLabel(label)
        self.lab.setFixedWidth(78)
        self.lab.setObjectName('sub')
        if tip:
            self.lab.setToolTip(tip + '\n（雙擊名稱還原預設值）')
        self.sl = QSlider(Qt.Horizontal)
        self.n = max(1, int(round((hi - lo) / step)))
        self.lo = lo
        self.sl.setRange(0, self.n)
        self.sp = QDoubleSpinBox()
        self.sp.setRange(lo, hi)
        self.sp.setDecimals(decimals)
        self.sp.setSingleStep(step)
        self.sp.setObjectName("num")
        self.sp.setFixedWidth(84)
        self.sp.setKeyboardTracking(False)
        l.addWidget(self.lab)
        l.addWidget(self.sl, 1)
        l.addWidget(self.sp)
        self._lock = False
        self.sl.valueChanged.connect(lambda v: self._set_display(self.lo + v * self.step, from_slider=True))
        self.sp.valueChanged.connect(lambda v: self._set_display(v))
        self.default = None

    def _set_display(self, d, from_slider=False):
        if self._lock:
            return
        self._lock = True
        if not from_slider:
            self.sl.setValue(int(round((d - self.lo) / self.step)))
        self.sp.setValue(d)
        self._lock = False
        self.changed.emit(d / self.scale)

    def set_value(self, v):
        self._lock = True
        d = v * self.scale
        self.sp.setValue(d)
        self.sl.setValue(int(round((d - self.lo) / self.step)))
        self._lock = False

    def mouseDoubleClickEvent(self, e):
        if self.default is not None and self.lab.geometry().contains(e.position().toPoint()):
            self.set_value(self.default)
            self.changed.emit(self.default)


def card(title, value='—'):
    f = QFrame()
    f.setObjectName('card')
    l = QVBoxLayout(f)
    l.setContentsMargins(12, 9, 12, 9)
    l.setSpacing(2)
    t = QLabel(title)
    t.setObjectName('card_title')
    v = QLabel(value)
    v.setObjectName('card_value')
    l.addWidget(t)
    l.addWidget(v)
    f.title_label, f.value_label = t, v
    return f


def app_icon() -> QIcon:
    """FusionStitch icon (design 043「FS 字母・玻璃」) at every size Windows asks for."""
    import base64
    import appicon
    icon = QIcon()
    for s_, data in appicon.PNG.items():
        pm = QPixmap()
        if pm.loadFromData(base64.b64decode(data), "PNG"):
            icon.addPixmap(pm)
    return icon


class Histogram(QWidget):
    """RGB + luminance histogram of the graded preview (fed with an (N, 3) uint8 array)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(70)
        self.curves = None
        self.clip_lo = self.clip_hi = 0.0

    def set_pixels(self, px):
        import numpy as np
        if px is None or len(px) == 0:
            return
        luma = (px @ np.array([0.2126, 0.7152, 0.0722])).astype(np.int32)
        hs = [np.bincount(px[:, i], minlength=256) for i in range(3)] + [np.bincount(np.clip(luma, 0, 255), minlength=256)]
        k = np.ones(5) / 5
        hs = [np.convolve(h.astype(float), k, mode="same") for h in hs]
        top = max(1.0, max(float(h[2:-2].max()) for h in hs))
        self.curves = [h / top for h in hs]
        n = len(px)
        self.clip_lo = float((luma <= 2).sum()) / n
        self.clip_hi = float((luma >= 253).sum()) / n
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QColor("#0b0f14"))
        p.setPen(QPen(QColor("#222933"), 1))
        for q in (0.25, 0.5, 0.75):
            x = r.left() + r.width() * q
            p.drawLine(int(x), r.top(), int(x), r.bottom())
        if not self.curves:
            return
        cols = [QColor(255, 80, 80, 110), QColor(80, 220, 120, 110), QColor(80, 150, 255, 110), QColor(235, 235, 235, 150)]
        for h, col in zip(self.curves, cols):
            path = QPainterPath(QPointF(r.left(), r.bottom()))
            for i in range(256):
                path.lineTo(r.left() + r.width() * i / 255, r.bottom() - r.height() * min(1.0, h[i]))
            path.lineTo(r.right(), r.bottom())
            path.closeSubpath()
            p.fillPath(path, col)
        f = QFont(self.font()); f.setPointSizeF(f.pointSizeF() * 0.8); p.setFont(f)
        if self.clip_lo > 0.02:
            p.setPen(QColor("#4dc9ff")); p.drawText(r.adjusted(4, 2, 0, 0), Qt.AlignLeft | Qt.AlignTop, f"暗部死黑 {self.clip_lo * 100:.0f}%")
        if self.clip_hi > 0.02:
            p.setPen(QColor("#ff6577")); p.drawText(r.adjusted(0, 2, -4, 0), Qt.AlignRight | Qt.AlignTop, f"亮部過曝 {self.clip_hi * 100:.0f}%")
