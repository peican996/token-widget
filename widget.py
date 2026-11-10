# -*- coding: utf-8 -*-
"""PySide6 桌面悬浮窗

视觉方向：克制的深色仪表盘 —— 一个大数字做主视觉，次要指标两列排布，
用细线与留白分隔（不做卡片堆叠），唯一强调色为绿色。
动效：数值变化淡入、折线绘入。
"""
from PySide6.QtCore import (
    Qt, QTimer, QPoint, QPointF, QVariantAnimation, QEasingCurve, QPropertyAnimation,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLayout, QMenu, QProgressBar, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

import config as cfg_mod
import storage

# 单一强调色 + 中性色阶
ACCENT = "#4cc38a"
TEXT = "#ececf1"
MUTED = "#8b8b95"
FAINT = "#5d5d67"
SURFACE = "rgba(18, 19, 22, 226)"

STYLE = """
#panel {
    background: %(surface)s;
    border-radius: 14px;
    border: 1px solid rgba(255,255,255,18);
}
QLabel { color: %(text)s; background: transparent; border: none; }
QLabel#title {
    color: %(muted)s;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.6px;
}
QLabel#label { color: %(muted)s; font-size: 11px; }
QLabel#caption { color: %(muted)s; font-size: 11px; }
QLabel#stat { color: #d7d7de; font-size: 16px; font-weight: 500; }
QLabel#mini { color: #c9c9d1; font-size: 13px; font-weight: 500; }
QLabel#detail { color: %(muted)s; font-size: 11px; }
QLabel#chevron { color: %(muted)s; font-size: 12px; }
QProgressBar {
    background: rgba(255,255,255,16);
    border: none;
    border-radius: 3px;
    height: 6px;
}
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }
QMenu {
    background: #202127; color: %(text)s;
    border: 1px solid rgba(255,255,255,26);
    border-radius: 8px; padding: 4px;
}
QMenu::item { padding: 6px 18px; border-radius: 5px; }
QMenu::item:selected { background: rgba(255,255,255,26); }
""" % {"surface": SURFACE, "text": TEXT, "muted": MUTED, "accent": ACCENT}


def fmt_tokens(n):
    if n >= 1_000_000_000:
        return "%.2fB" % (n / 1_000_000_000)
    if n >= 1_000_000:
        return "%.2fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.1fK" % (n / 1_000)
    return str(n)


def fmt_usd(v):
    return "--" if v is None else "$%.2f" % v


def fmt_credit_val(v):
    """积分数值：小额保留 1 位小数，其余用千分位完整展示"""
    v = v or 0
    if v < 10:
        return "%.1f" % v
    return "{:,.0f}".format(v)


SOURCE_LABELS = {
    "claude_code": "Claude Code",
    "workbuddy": "WorkBuddy",
    "trae_api": "Trae",
    "openai_api": "OpenAI",
    "oneapi": "One-API",
    "trae": "Trae",
}

# 图表系列色：主来源用强调绿，后续来源用蓝/紫/琥珀
CHART_COLORS = [QColor("#4cc38a"), QColor("#4f8bff"),
                QColor("#b48cff"), QColor("#f2b35c")]

def src_name(source):
    return SOURCE_LABELS.get(source, source)


def hero_html(value, unit="tok"):
    return ("<span style='font-size:27px;font-weight:600;color:%s'>%s</span>"
            "<span style='font-size:11px;color:%s'> %s</span>" % (TEXT, value, MUTED, unit))


class MetricRow(QWidget):
    """积分行：色点 + 名称 + 若干「标签 数值」对，保证多行结构一致"""

    def __init__(self, pairs=2, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.name = QLabel("")
        self.name.setObjectName("label")
        lay.addWidget(self.dot)
        lay.addWidget(self.name)
        lay.addStretch(1)
        self.pairs = []
        for _ in range(pairs):
            lab = QLabel("")
            lab.setObjectName("detail")
            val = QLabel("")
            val.setObjectName("mini")
            lay.addSpacing(8)
            lay.addWidget(lab)
            lay.addWidget(val)
            self.pairs.append((lab, val))

    def set_data(self, name, pairs, color):
        self.dot.setStyleSheet("background: %s; border-radius: 4px;" % color.name())
        self.name.setText(name)
        for i, (lab, val) in enumerate(self.pairs):
            if i < len(pairs):
                lab.setText(pairs[i][0])
                val.setText(pairs[i][1])
                lab.setVisible(True)
                val.setVisible(True)
            else:
                lab.setVisible(False)
                val.setVisible(False)


def _tray_icon():
    """程序化生成托盘图标，避免依赖外部 ico 文件"""
    pm = QPixmap(32, 32)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 28, 28, 8, 8)
    p.setPen(QColor("#14151a"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(16)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    p.end()
    return QIcon(pm)


def divider():
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(255,255,255,22); border: none;")
    return line


class Sparkline(QWidget):
    """近 N 天用量折线，带绘入动画"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self._progress = 1.0
        self.setFixedHeight(44)
        self.setMinimumWidth(210)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_progress)

    def _on_progress(self, v):
        self._progress = float(v)
        self.update()

    def set_values(self, values):
        """仅在首次出现数据时做一次绘入，后续静默更新"""
        first = (not self.values) and bool(values)
        self.values = list(values)
        if first and max(self.values, default=0) > 0:
            self._progress = 0.0
            self._anim.stop()
            self._anim.start()
        else:
            self._progress = 1.0
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_x, pad_y = 1.0, 8.0
        vals = self.values
        if len(vals) < 2 or max(vals) <= 0:
            painter.setPen(QColor(FAINT))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无数据")
            return
        mx = max(vals)
        n = len(vals)
        step = (w - pad_x * 2) / (n - 1)
        pts = [QPointF(pad_x + i * step,
                       h - pad_y - (v / mx) * (h - pad_y * 2)) for i, v in enumerate(vals)]

        # 绘入进度：按裁剪宽度推进
        clip_w = pad_x + (w - pad_x * 2) * self._progress
        painter.setClipRect(0, 0, int(clip_w), h)

        path = QPainterPath()
        path.moveTo(pts[0].x(), h)
        for p in pts:
            path.lineTo(p)
        path.lineTo(pts[-1].x(), h)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(76, 195, 138, 34))
        painter.drawPath(path)

        pen = QPen(QColor(ACCENT), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for i in range(n - 1):
            painter.drawLine(pts[i], pts[i + 1])

        painter.setClipping(False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        painter.drawEllipse(pts[-1], 2.5, 2.5)


class QuotaRow(QWidget):
    """额度进度：名称 + 数值 + 细进度条"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel("")
        self.name.setObjectName("label")
        self.value = QLabel("")
        self.value.setObjectName("label")
        top.addWidget(self.name)
        top.addStretch(1)
        top.addWidget(self.value)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addLayout(top)
        lay.addWidget(self.bar)

    def set_data(self, label, used, total, money=True):
        self.name.setText(label or "额度")

        def v(x):
            if x is None:
                return "--"
            if money:
                return fmt_usd(x)
            # 积分等计数型额度：整数 + 千位分隔，避免 4,100 被压成 “4.1K”
            return "{:,.0f}".format(x)

        if total and total > 0:
            if used is not None:
                pct = max(0.0, min(1.0, used / total))
                self.bar.setValue(int(pct * 1000))
                self.value.setText("%s / %s" % (v(used), v(total)))
            else:
                self.bar.setValue(0)
                self.value.setText("剩余 %s" % v(total))
        elif used is not None:
            self.bar.setValue(0)
            self.value.setText("已用 %s" % v(used))
        else:
            self.bar.setValue(0)
            self.value.setText("--")


def fmt_ball(n):
    """球形态的超短数值：最多 4 个字符"""
    if n >= 1_000_000:
        return "%.0fM" % (n / 1_000_000)
    if n >= 1_000:
        return "%.0fK" % (n / 1_000)
    return str(int(n))


class BallView(QWidget):
    """收起后的圆形小球：今日用量 + 进度环，点击展开"""

    SIZE = 58

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setToolTip("单击展开面板，拖动可移动")
        self.value = 0
        self.ratio = 0.0

    def set_data(self, tokens, ratio):
        self.value = tokens
        self.ratio = max(0.0, min(1.0, ratio))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        painter.setPen(QPen(QColor(255, 255, 255, 34), 1))
        painter.setBrush(QColor(18, 19, 22, 226))
        painter.drawEllipse(rect)

        ring = rect.adjusted(5, 5, -5, -5)
        painter.setPen(QPen(QColor(255, 255, 255, 32), 2))
        painter.drawEllipse(ring)
        if self.ratio > 0:
            painter.setPen(QPen(QColor(ACCENT), 2))
            painter.drawArc(ring, 90 * 16, -int(360 * self.ratio * 16))

        painter.setPen(QColor(TEXT))
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect.adjusted(0, 4, 0, 0),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         fmt_ball(self.value))

        painter.setPen(QColor(MUTED))
        f2 = QFont()
        f2.setPixelSize(7)
        painter.setFont(f2)
        painter.drawText(rect.adjusted(0, 0, 0, -11),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                         "tok")


class TokenWidget(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._drag_pos = None
        self._expanded = False
        self._last_today = None
        self._ball_mode = False
        self._press_global = None
        self._moved = False

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if cfg["ui"].get("always_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(float(cfg["ui"].get("opacity", 0.8)))

        self.panel = panel = QFrame(self)
        panel.setObjectName("panel")
        panel.setStyleSheet(STYLE)
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 150))
        panel.setGraphicsEffect(shadow)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(panel)
        root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        self.ball = BallView()
        self.ball.setVisible(False)
        root.addWidget(self.ball)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(18, 15, 18, 15)
        lay.setSpacing(0)

        # ---- 顶部：标题 + 状态 + 展开 ----
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        title = QLabel("TOKEN 用量")
        title.setObjectName("title")
        dot = QLabel()
        dot.setFixedSize(6, 6)
        dot.setStyleSheet("background: %s; border-radius: 3px;" % ACCENT)
        live = QLabel("实时")
        live.setObjectName("label")
        self.btn_expand = QLabel("▸")
        self.btn_expand.setObjectName("chevron")
        self.btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expand.mousePressEvent = lambda e: self.toggle_expand()
        self.btn_ball = QLabel("○")
        self.btn_ball.setObjectName("chevron")
        self.btn_ball.setToolTip("收起为球")
        self.btn_ball.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ball.mousePressEvent = lambda e: self.set_ball_mode(
            True, e.globalPosition().toPoint())
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(dot)
        title_row.addWidget(live)
        title_row.addSpacing(2)
        title_row.addWidget(self.btn_ball)
        title_row.addSpacing(2)
        title_row.addWidget(self.btn_expand)
        lay.addLayout(title_row)
        lay.addSpacing(14)

        # ---- 主视觉：今日总量 ----
        self.metric_box = QWidget()
        mb = QVBoxLayout(self.metric_box)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(3)
        self.lbl_value = QLabel(hero_html("0"))
        self.lbl_value.setTextFormat(Qt.TextFormat.RichText)
        mb.addWidget(self.lbl_value)
        lay.addWidget(self.metric_box)
        lay.addSpacing(14)

        # ---- 次指标：本月 / 累计（两列） ----
        cols = QHBoxLayout()
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(28)
        self.lbl_month = self._stat_block(cols, "本月")
        self.lbl_all = self._stat_block(cols, "累计")
        lay.addLayout(cols)
        lay.addSpacing(14)

        # ---- 积分消耗：WorkBuddy 与 Trae 并列成组 ----
        self.credits_box = QWidget()
        cb = QVBoxLayout(self.credits_box)
        cb.setContentsMargins(0, 0, 0, 0)
        cb.setSpacing(5)
        lbl_credits = QLabel("积分消耗")
        lbl_credits.setObjectName("label")
        cb.addWidget(lbl_credits)
        self.cred_wb = MetricRow(pairs=3)
        self.cred_trae = MetricRow(pairs=3)
        cb.addWidget(self.cred_wb)
        cb.addWidget(self.cred_trae)
        lay.addWidget(self.credits_box)
        lay.addSpacing(16)

        # ---- 趋势 ----
        trend_head = QHBoxLayout()
        trend_head.setContentsMargins(0, 0, 0, 0)
        lbl_trend = QLabel("近 7 天")
        lbl_trend.setObjectName("label")
        self.lbl_peak = QLabel("")
        self.lbl_peak.setObjectName("label")
        trend_head.addWidget(lbl_trend)
        trend_head.addStretch(1)
        trend_head.addWidget(self.lbl_peak)
        lay.addLayout(trend_head)
        lay.addSpacing(4)
        self.spark = Sparkline()
        lay.addWidget(self.spark)

        # ---- 额度（有数据才显示） ----
        self.quota_box = QWidget()
        ql = QVBoxLayout(self.quota_box)
        ql.setContentsMargins(0, 12, 0, 0)
        ql.setSpacing(10)
        ql.addWidget(divider())
        self.quota_main = QuotaRow()
        ql.addWidget(self.quota_main)
        lay.addWidget(self.quota_box)

        # ---- 展开区 ----
        self.detail_box = QWidget()
        db = QVBoxLayout(self.detail_box)
        db.setContentsMargins(0, 12, 0, 0)
        db.setSpacing(10)
        db.addWidget(divider())
        self.detail = QWidget()
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(10)
        brk = QHBoxLayout()
        brk.setContentsMargins(0, 0, 0, 0)
        brk.setSpacing(24)
        self.brk_in = self._mini_stat(brk, "输入")
        self.brk_out = self._mini_stat(brk, "输出")
        self.brk_cache = self._mini_stat(brk, "缓存读")
        dl.addLayout(brk)
        self.quota_extra = []
        self._detail_layout = dl
        db.addWidget(self.detail)
        lay.addWidget(self.detail_box)
        self.detail_box.setVisible(False)

        # ---- 托盘 ----
        self.tray = QSystemTrayIcon(_tray_icon(), self)
        self.tray.setToolTip("Token 用量监控")
        tray_menu = QMenu()
        act_show = tray_menu.addAction("显示 / 隐藏")
        act_show.triggered.connect(self.toggle_visible)
        act_quit = tray_menu.addAction("退出")
        act_quit.triggered.connect(QApplication.quit)
        self.tray.setContextMenu(tray_menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        # 定时刷新
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.reload)
        self.timer.start(max(3, int(cfg["ui"].get("refresh_seconds", 15))) * 1000)

        pos = cfg["ui"].get("pos")
        if isinstance(pos, list) and len(pos) == 2:
            self.move(pos[0], pos[1])
        else:
            self.move(60, 60)
        self.reload()
        if cfg["ui"].get("ball_mode"):
            # 窗口显示后布局才会收缩到球的尺寸
            QTimer.singleShot(0, lambda: self.set_ball_mode(True))

    def _stat_block(self, parent_layout, label_text, value_style="stat"):
        """次要指标：小标签在上，数值在下"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        lab = QLabel(label_text)
        lab.setObjectName("label")
        val = QLabel("--")
        val.setObjectName(value_style)
        v.addWidget(lab)
        v.addWidget(val)
        parent_layout.addWidget(box)
        return val

    def _mini_stat(self, parent_layout, label_text):
        return self._stat_block(parent_layout, label_text, value_style="mini")

    def _set_value_text(self, n):
        self.lbl_value.setText(hero_html(fmt_tokens(n)))

    # ---------- 数据 ----------
    def reload(self):
        day = storage.aggregate("day")
        month = storage.aggregate("month")
        alltime = storage.aggregate("all")

        self._set_value_text(day["total_tokens"])
        self.lbl_month.setText(fmt_tokens(month["total_tokens"]))
        self.lbl_all.setText(fmt_tokens(alltime["total_tokens"]))

        series = storage.daily_series(7)
        self.spark.set_values([s["tokens"] for s in series])
        peak = max((s["tokens"] for s in series), default=0)
        self.lbl_peak.setText(("峰值 %s" % fmt_tokens(peak)) if peak else "")
        ratio = (day["total_tokens"] / peak) if peak else 0.0
        self.ball.set_data(day["total_tokens"], ratio)

        # Trae 是积分制，与 WorkBuddy 积分并列放在「积分消耗」组
        snaps = storage.latest_quota_snapshots()
        trae = next((s for s in snaps if s["source"] in ("trae_api", "trae")), None)
        show_trae = bool(trae and trae.get("total_usd"))
        if show_trae:
            used = trae.get("used_usd") or 0
            total = trae["total_usd"]
            trae_today = storage.quota_used_today(trae["source"])
            self.cred_trae.set_data(
                "Trae",
                [("今日", "--" if trae_today is None else fmt_credit_val(trae_today)),
                 ("已用", "{:,.0f}".format(used)),
                 ("额度", "{:,.0f}".format(total))],
                QColor(MUTED))
        self.cred_trae.setVisible(show_trae)

        wb_quota = next((s for s in snaps if s["source"] == "workbuddy_quota"), None)
        show_wb = bool((wb_quota and wb_quota.get("total_usd"))
                       or day["credits"] or alltime["credits"])
        if show_wb:
            if wb_quota and wb_quota.get("total_usd"):
                used = wb_quota.get("used_usd") or 0
                remain = max(0.0, wb_quota["total_usd"] - used)
                used_txt, remain_txt = fmt_credit_val(used), fmt_credit_val(remain)
            else:
                used_txt, remain_txt = fmt_credit_val(alltime["credits"]), "--"
            self.cred_wb.set_data(
                "WorkBuddy",
                [("今日", fmt_credit_val(day["credits"])),
                 ("已用", used_txt),
                 ("剩余", remain_txt)],
                CHART_COLORS[0])
        self.cred_wb.setVisible(show_wb)
        self.credits_box.setVisible(show_trae or show_wb)

        # 其他额度型来源（OpenAI / One-API 等）仍用独立进度条
        others = [s for s in snaps
                  if s["source"] not in ("trae", "trae_api", "workbuddy_quota")]
        if others:
            self.quota_box.setVisible(True)
            self.quota_main.set_data(
                others[0]["label"] or src_name(others[0]["source"]),
                others[0]["used_usd"], others[0]["total_usd"],
                money=others[0]["source"] not in ("trae", "trae_api"))
        else:
            self.quota_box.setVisible(False)

        if self._expanded:
            self.brk_in.setText(fmt_tokens(day["input_tokens"]))
            self.brk_out.setText(fmt_tokens(day["output_tokens"]))
            self.brk_cache.setText(fmt_tokens(day["cache_read_tokens"]))
            extra = others[1:]
            while len(self.quota_extra) < len(extra):
                row = QuotaRow()
                self._detail_layout.addWidget(row)
                self.quota_extra.append(row)
            for i, row in enumerate(self.quota_extra):
                if i < len(extra):
                    s = extra[i]
                    row.setVisible(True)
                    row.set_data(
                        s["label"] or src_name(s["source"]), s["used_usd"], s["total_usd"],
                        money=s["source"] not in ("trae", "trae_api"))
                else:
                    row.setVisible(False)

        # 数值变化直接更新，不做滚动动画，避免分散注意力
        self.adjustSize()

    # ---------- 交互 ----------
    def set_ball_mode(self, on, center_at=None):
        """球形态：只留一个小圆球，点击展开；收起时以点击位置为球心"""
        on = bool(on)
        self._ball_mode = on
        self.panel.setVisible(not on)
        self.ball.setVisible(on)
        self.cfg["ui"]["ball_mode"] = on
        cfg_mod.save_config(self.cfg)
        self.adjustSize()
        if on and center_at is not None:
            self.move(center_at.x() - (self.width() - 1) // 2,
                      center_at.y() - (self.height() - 1) // 2)
        self._clamp_to_screen()
        p = self.pos()
        self.cfg["ui"]["pos"] = [p.x(), p.y()]
        cfg_mod.save_config(self.cfg)
        self.reload()

    def _clamp_to_screen(self):
        scr = self.screen().availableGeometry() if self.screen() else None
        if not scr:
            return
        g = self.frameGeometry()
        x = min(max(g.left(), scr.left()), scr.right() - g.width())
        y = min(max(g.top(), scr.top()), scr.bottom() - g.height())
        self.move(x, y)

    def toggle_expand(self):
        self._expanded = not self._expanded
        self.detail_box.setVisible(self._expanded)
        self.btn_expand.setText("▾" if self._expanded else "▸")
        self.reload()

    def toggle_visible(self):
        self.setVisible(not self.isVisible())

    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.toggle_visible()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global = e.globalPosition().toPoint()
            self._moved = False
            e.accept()
        elif e.button() == Qt.MouseButton.RightButton:
            self._show_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._press_global is not None:
                delta = e.globalPosition().toPoint() - self._press_global
                if abs(delta.x()) > 4 or abs(delta.y()) > 4:
                    self._moved = True
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        if self._drag_pos is not None:
            self._drag_pos = None
            p = self.pos()
            self.cfg["ui"]["pos"] = [p.x(), p.y()]
            cfg_mod.save_config(self.cfg)
        if self._ball_mode and self._press_global is not None and not self._moved:
            self.set_ball_mode(False)
        self._press_global = None

    def _show_menu(self, global_pos):
        m = QMenu(self)
        act_refresh = m.addAction("立即刷新")
        act_refresh.triggered.connect(self.reload)
        act_ball = m.addAction("球形态")
        act_ball.setCheckable(True)
        act_ball.setChecked(self._ball_mode)
        act_ball.triggered.connect(
            lambda checked: self.set_ball_mode(checked, QCursor.pos()))
        act_top = m.addAction("置顶")
        act_top.setCheckable(True)
        act_top.setChecked(bool(self.cfg["ui"].get("always_on_top", True)))
        act_top.triggered.connect(self._toggle_top)

        sub = m.addMenu("透明度")
        for label, val in [("50%", 0.5), ("70%", 0.7), ("85%", 0.85), ("100%", 1.0)]:
            a = sub.addAction(label)
            a.triggered.connect(lambda checked=False, v=val: self._set_opacity(v))

        act_auto = m.addAction("开机自启")
        act_auto.setCheckable(True)
        act_auto.setChecked(bool(self.cfg["ui"].get("autostart", False)))
        act_auto.triggered.connect(self._toggle_autostart)

        m.addSeparator()
        act_quit = m.addAction("退出")
        act_quit.triggered.connect(QApplication.quit)
        m.exec(global_pos)

    def _toggle_top(self, checked):
        self.cfg["ui"]["always_on_top"] = bool(checked)
        cfg_mod.save_config(self.cfg)
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _set_opacity(self, v):
        self.cfg["ui"]["opacity"] = v
        cfg_mod.save_config(self.cfg)
        self.setWindowOpacity(v)

    def _toggle_autostart(self, checked):
        try:
            cfg_mod.set_autostart(bool(checked))
            self.cfg["ui"]["autostart"] = bool(checked)
            cfg_mod.save_config(self.cfg)
        except Exception:
            pass
