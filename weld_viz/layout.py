"""Окно «Компоновка листа»: лист этапа как в PDF, окошки уточнений перетаскиваются и меняют размер мышью."""
from __future__ import annotations

import copy

from qtpy.QtCore import QPointF, QRectF, Qt, Signal
from qtpy.QtGui import QColor, QCursor, QFont, QImage, QPainter, QPen, QPixmap
from qtpy.QtWidgets import (QApplication, QDialog, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton,
                            QSizePolicy, QVBoxLayout, QWidget)

from . import details as lay
from . import theme
from .scenario import Scenario, clamp_rect, detail_letter
from .scene import SceneBuilder

PREVIEW_SCALE = 1.4    # пикселей снимка на пункт листа в окне компоновки
HANDLE = 12            # размер уголка «потянуть» в пикселях экрана
ARROW_GRAB = 9         # радиус захвата конца стрелки, пиксели
ARROW_EDIT = "#F57C00"  # стрелка, которую тянут


def to_pixmap(img) -> QPixmap:
    h, w = img.shape[:2]
    buf = img[:, :, :3].tobytes()
    q = QImage(buf, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(q.copy())


class SheetCanvas(QWidget):
    """Лист этапа. Окошки двигаются мышью, правый нижний уголок — размер. Обводка места на общем виде
    двигается за середину, размер — за край."""
    selected = Signal(int)
    changed = Signal()
    resized_detail = Signal(int)
    menu_requested = Signal(int, object)
    arrow_moved = Signal(int, str, float, float)   # № стрелки, конец anchor/free, сдвиг в долях картинки
    arrow_menu = Signal(int, object)

    ASPECT = lay.IMG_W / lay.IMG_H

    def __init__(self, details, title: str, parent=None):
        super().__init__(parent)
        self.details = details
        self.title = title
        self.main_pm: QPixmap | None = None
        self.inset_pm: dict[int, QPixmap] = {}
        self.marks: list = []        # места уточнений по их ракурсам (до ручной правки)
        self.arrows: list = []       # стрелки общего вида (SceneBuilder.last_arrows)
        self._arrow_preview = None   # (№ стрелки, конец, dx, dy) — пока тянут
        self.current = 0 if details else -1
        self._drag = None   # (индекс, режим, точка нажатия в долях картинки, исходное значение)
        self.setMouseTracking(True)
        self.setMinimumSize(760, 540)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # --- геометрия: лист вписан в виджет
    def _page(self) -> tuple[float, float, float]:
        k = min((self.width() - 24) / lay.PAGE_W, (self.height() - 24) / lay.PAGE_H)
        return (self.width() - lay.PAGE_W * k) / 2, (self.height() - lay.PAGE_H * k) / 2, k

    def img_rect(self) -> QRectF:
        ox, oy, k = self._page()
        return QRectF(ox + lay.IMG_X * k, oy + lay.IMG_TOP * k, lay.IMG_W * k, lay.IMG_H * k)

    def inset_rect(self, d) -> QRectF:
        r = self.img_rect()
        x, y, w, h = d.rect
        return QRectF(r.x() + x * r.width(), r.y() + y * r.height(), w * r.width(), h * r.height())

    def raw_mark(self, j):
        return self.marks[j] if j < len(self.marks) else None

    def mark(self, j):
        """Обводка уточнения j на общем виде (x, y, r) — как в PDF."""
        return lay.place_mark(self.raw_mark(j), self.details[j], self.ASPECT)

    def final_marks(self) -> list:
        return [self.mark(j) for j in range(len(self.details))]

    def _mark_geom(self, mk):
        """Центр, радиус и кружок с буквой обводки в пикселях виджета."""
        ir = self.img_rect()
        c = QPointF(ir.x() + mk[0] * ir.width(), ir.y() + mk[1] * ir.height())
        r = mk[2] * ir.height()
        bx, by = lay.badge_pos(mk, [d.rect for d in self.details], self.ASPECT)
        return c, r, QPointF(ir.x() + bx * ir.width(), ir.y() + by * ir.height())

    def _frac(self, pos) -> tuple[float, float]:
        r = self.img_rect()
        return (pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height()

    def _arrow_point(self, a, end, preview=None) -> QPointF:
        ir = self.img_rect()
        x, y = a[end]
        if preview:
            _, pend, dx, dy = preview
            if pend == "anchor" or pend == end:   # конец у детали тянет всю стрелку
                x, y = x + dx, y + dy
        return QPointF(ir.x() + x * ir.width(), ir.y() + y * ir.height())

    def _hit(self, pos):
        for i in reversed(range(len(self.details))):   # окошки — поверх обводок и стрелок
            r = self.inset_rect(self.details[i])
            if QRectF(r.right() - HANDLE, r.bottom() - HANDLE, HANDLE + 3, HANDLE + 3).contains(pos):
                return i, "size"
            if r.contains(pos):
                return i, "move"
        ir = self.img_rect()
        best = None
        for i, a in enumerate(self.arrows):
            for end in ("free", "anchor"):
                pt = self._arrow_point(a, end)
                if not ir.contains(pt):
                    continue
                dist = ((pos.x() - pt.x()) ** 2 + (pos.y() - pt.y()) ** 2) ** 0.5
                if dist <= ARROW_GRAB and (best is None or dist < best[2]):
                    best = (i, "arrow_" + end, dist)
        if best:
            return best[0], best[1]
        _, _, k = self._page()
        best = None
        for i in range(len(self.details)):
            mk = self.mark(i)
            if not mk:
                continue
            c, r, b = self._mark_geom(mk)
            dist = ((pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2) ** 0.5
            if ((pos.x() - b.x()) ** 2 + (pos.y() - b.y()) ** 2) ** 0.5 <= 8 * k:
                return i, "mark_move"
            if abs(dist - r) <= 7:
                return i, "mark_size"
            if dist < r and (best is None or r < best[1]):   # из вложенных — меньшая
                best = (i, r)
        return (best[0], "mark_move") if best else (None, None)

    # --- рисование
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(theme.BG))
        ox, oy, k = self._page()
        page = QRectF(ox, oy, lay.PAGE_W * k, lay.PAGE_H * k)
        p.fillRect(page.translated(3, 3), QColor(0, 0, 0, 40))
        p.fillRect(page, QColor("white"))
        m = lay.MARGIN * k
        # заголовок
        p.fillRect(QRectF(ox + m, oy + m - 3 * k, 6 * k, 18 * k), QColor(theme.RED))
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(9, int(15 * k)))
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QPointF(ox + m + 14 * k, oy + m + 12 * k), self.title)
        p.setPen(QPen(QColor(theme.RED), max(1.0, 1.6 * k)))
        p.drawLine(QPointF(ox + m, oy + m + 22 * k), QPointF(ox + page.width() - m, oy + m + 22 * k))
        # правая колонка — условно
        f.setPixelSize(max(8, int(10 * k)))
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        sx = ox + (lay.PAGE_W - lay.MARGIN - lay.SIDE_W) * k
        for n, text in enumerate(("Заложить детали:", "Прижимы и подвижные элементы:")):
            y = oy + (lay.MARGIN + 50 + n * 90) * k
            p.drawText(QPointF(sx, y), text)
            for j in range(3):
                p.fillRect(QRectF(sx + 12 * k, y + (10 + j * 14) * k, (150 - j * 25) * k, 6 * k), QColor(theme.GRID))
        # общий вид
        ir = self.img_rect()
        if self.main_pm:
            p.drawPixmap(ir, self.main_pm, QRectF(self.main_pm.rect()))
        else:
            p.setPen(QColor(theme.MUTED))
            p.drawText(ir, Qt.AlignCenter, "Отрисовка…")
        p.setPen(QPen(QColor("#CCCCCC"), 1))
        p.drawRect(ir)
        blue = QColor(lay.CALLOUT)
        sel_color = QColor(theme.RED)
        p.save()
        p.setClipRect(ir)
        for j, d in enumerate(self.details):
            mk = self.mark(j)
            if not mk:
                continue
            c, r, b = self._mark_geom(mk)
            sel = j == self.current
            p.setPen(QPen(blue, max(1.2, 1.4 * k) * (1.6 if sel else 1)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r, r)
            ln = lay.leader(mk, d.rect, self.ASPECT)
            if ln:
                pen = QPen(blue, max(1.0, 0.8 * k))
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawLine(QPointF(ir.x() + ln[0][0] * ir.width(), ir.y() + ln[0][1] * ir.height()),
                           QPointF(ir.x() + ln[1][0] * ir.width(), ir.y() + ln[1][1] * ir.height()))
            if sel:   # за что тянуть: середина — сдвиг, край — размер
                p.setPen(QPen(sel_color, 1, Qt.DashLine))
                p.drawEllipse(c, r + 4, r + 4)
                g = QPointF(c.x() + r * 0.71, c.y() + r * 0.71)
                p.setPen(Qt.NoPen)
                p.setBrush(sel_color)
                p.drawRect(QRectF(g.x() - 4, g.y() - 4, 8, 8))
            p.setPen(Qt.NoPen)
            p.setBrush(blue)
            p.drawEllipse(b, 6.5 * k, 6.5 * k)
            p.setPen(QColor("white"))
            f.setPixelSize(max(8, int(9 * k)))
            p.setFont(f)
            p.drawText(QRectF(b.x() - 7 * k, b.y() - 7 * k, 14 * k, 14 * k), Qt.AlignCenter, detail_letter(j))
        # концы стрелок: квадрат — у детали (тянет всю стрелку), круг — свободный конец с подписью
        ink = QColor(theme.GRAPHITE)
        for i, a in enumerate(self.arrows):
            pv = self._arrow_preview if self._arrow_preview and self._arrow_preview[0] == i else None
            pa, pf = self._arrow_point(a, "anchor", pv), self._arrow_point(a, "free", pv)
            if pv:
                pen = QPen(QColor(ARROW_EDIT), 3)
                pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawLine(pf, pa)
            p.setPen(QPen(QColor(ARROW_EDIT) if pv else ink, 1.5))
            p.setBrush(QColor(255, 255, 255, 220))
            p.drawRect(QRectF(pa.x() - 4, pa.y() - 4, 8, 8))
            p.drawEllipse(pf, 5, 5)
        p.restore()
        # окошки
        for j, d in enumerate(self.details):
            r = self.inset_rect(d)
            p.fillRect(r, QColor("white"))
            pm = self.inset_pm.get(j)
            if pm:
                p.drawPixmap(r, pm, QRectF(pm.rect()))
            else:
                p.setPen(QColor(theme.MUTED))
                p.drawText(r, Qt.AlignCenter, "…")
            sel = j == self.current
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(sel_color if sel else QColor(theme.TEXT), 2.5 if sel else 1))
            p.drawRect(r)
            f.setPixelSize(max(8, int(8.5 * k)))
            p.setFont(f)
            text = lay.detail_label(j, d)
            fm = p.fontMetrics()
            tw = min(r.width(), (fm.horizontalAdvance(text) if hasattr(fm, "horizontalAdvance") else fm.width(text))
                     + 10 * k)
            tab = QRectF(r.x(), r.y(), tw, 14 * k)
            p.fillRect(tab, blue)
            p.setPen(QColor("white"))
            p.drawText(tab.adjusted(5 * k, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
            if sel:   # уголок для изменения размера
                h = QRectF(r.right() - HANDLE, r.bottom() - HANDLE, HANDLE, HANDLE)
                p.fillRect(h, sel_color)
        p.end()

    # --- мышь
    def mousePressEvent(self, e):
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        i, mode = self._hit(pos)
        if i is None:
            return
        gpos = e.globalPos() if hasattr(e, "globalPos") else e.globalPosition().toPoint()
        if mode.startswith("arrow"):
            if e.button() == Qt.RightButton:
                self.arrow_menu.emit(i, gpos)
                return
            self._drag = (i, mode, self._frac(pos), None)
            self._arrow_preview = (i, mode[6:], 0.0, 0.0)
            self.update()
            return
        self.current = i
        self.selected.emit(i)
        if e.button() == Qt.RightButton:
            self.update()
            self.menu_requested.emit(i, gpos)
            return
        start = self.mark(i) if mode.startswith("mark") else list(self.details[i].rect)
        self._drag = (i, mode, self._frac(pos), start)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        if not self._drag:
            _, mode = self._hit(pos)
            self.setCursor(QCursor(Qt.SizeFDiagCursor if mode in ("size", "mark_size") else
                                   Qt.SizeAllCursor if mode in ("move", "mark_move", "arrow_anchor") else
                                   Qt.CrossCursor if mode == "arrow_free" else Qt.ArrowCursor))
            return
        i, mode, (fx0, fy0), v0 = self._drag
        fx, fy = self._frac(pos)
        dx, dy = fx - fx0, fy - fy0
        if mode.startswith("arrow"):
            self._arrow_preview = (i, mode[6:], dx, dy)
            self.update()
            return
        d = self.details[i]
        if mode == "mark_move":
            x, y = min(max(v0[0] + dx, 0.0), 1.0), min(max(v0[1] + dy, 0.0), 1.0)
            d.mark_adj = lay.adjust_for(self.raw_mark(i), x, y, v0[2], self.ASPECT)
        elif mode == "mark_size":
            r = ((fx - v0[0]) ** 2 * self.ASPECT ** 2 + (fy - v0[1]) ** 2) ** 0.5
            d.mark_adj = lay.adjust_for(self.raw_mark(i), v0[0], v0[1], min(r, 0.6), self.ASPECT)
        elif mode == "move":
            d.rect = clamp_rect([v0[0] + dx, v0[1] + dy, v0[2], v0[3]])
        else:
            d.rect = clamp_rect([v0[0], v0[1], min(v0[2] + dx, 1 - v0[0]), min(v0[3] + dy, 1 - v0[1])])
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag and self._drag[1].startswith("arrow"):
            _, end, dx, dy = self._arrow_preview
            i = self._drag[0]
            self._drag = None
            if abs(dx) + abs(dy) > 0.002:
                self.arrow_moved.emit(i, end, dx, dy)   # окно перерисует лист, потом уберёт подсказку
            else:
                self._arrow_preview = None
                self.update()
            return
        if self._drag:
            i, mode, _, v0 = self._drag
            self._drag = None
            now = self.mark(i) if mode.startswith("mark") else self.details[i].rect
            if now != v0:
                self.changed.emit()
                if mode == "size":
                    self.resized_detail.emit(i)

    def mouseDoubleClickEvent(self, e):
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        i, mode = self._hit(pos)
        if i is not None and not mode.startswith("arrow"):
            self.menu_requested.emit(-1 - i, None)   # двойной щелчок — подпись


class LayoutDialog(QDialog):
    """Компоновка листа этапа: окошки уточнений, обводки, стрелки. Правки — в копии этапа;
    после «Готово» — result_details и result_arrows."""

    def __init__(self, parent, scenario: Scenario, cls, scene, step_idx: int, current: int = 0):
        super().__init__(parent)
        self.setWindowTitle(f"Компоновка листа — этап {step_idx + 1}")
        self.cls, self.scene, self.step_idx = cls, scene, step_idx
        step = scenario.steps[step_idx]
        self.work = copy.deepcopy(step)
        self.result_details = self.work.details
        self.result_arrows = self.work.arrows
        self.scenario = copy.copy(scenario)   # тот же сценарий, но с копией этого этапа — для отрисовки правок
        self.scenario.steps = list(scenario.steps)
        self.scenario.steps[step_idx] = self.work
        title = f"Этап {step_idx + 1} из {len(scenario.steps)}" + (f" — {step.name}" if step.name else "")
        self.canvas = SheetCanvas(self.result_details, title, self)
        self.canvas.current = min(max(current, 0), len(self.result_details) - 1)
        self.canvas.changed.connect(self._on_changed)
        self.canvas.resized_detail.connect(self._render_inset)
        self.canvas.menu_requested.connect(self._menu)
        self.canvas.arrow_moved.connect(self._arrow_moved)
        self.canvas.arrow_menu.connect(self._arrow_menu)

        v = QVBoxLayout(self)
        hint = QLabel("Окошко перетаскивайте мышью, размер — за уголок справа внизу. Синий круг — место уточнения: "
                      "тащите за середину или букву, размер — за край. Стрелки: квадратик у детали — переместить "
                      "стрелку целиком, кружок у подписи — повернуть и изменить длину. Правый клик — меню. "
                      "Так же лист выйдет в PDF.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        b_auto = QPushButton("Расставить по углам")
        b_auto.setToolTip("Разнести окошки по свободным углам картинки, подальше от обведённых мест")
        b_auto.clicked.connect(self._auto)
        row.addWidget(b_auto)
        b_arrows = QPushButton("Стрелки по умолчанию")
        b_arrows.setToolTip("Вернуть все стрелки этапа туда, где их ставит программа")
        b_arrows.clicked.connect(self._reset_arrows)
        row.addWidget(b_arrows)
        row.addStretch()
        b_ok = QPushButton("Готово")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self.accept)
        b_cancel = QPushButton("Отмена")
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_ok)
        row.addWidget(b_cancel)
        v.addLayout(row)
        self.resize(1180, 860)

    def showEvent(self, e):
        super().showEvent(e)
        if self.canvas.main_pm is None:
            from qtpy.QtCore import QTimer
            QTimer.singleShot(50, self.render_all)

    def render_all(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            k = PREVIEW_SCALE
            scale = max(1.0, lay.IMG_W * lay.RENDER_SCALE / 1000) * k / lay.RENDER_SCALE
            img, marks = self.scene.render_step(self.scenario, self.cls, self.step_idx, (lay.IMG_W * k, lay.IMG_H * k),
                                                marks=[d.camera for d in self.result_details], scale=max(scale, 1.0),
                                                aa=False)
            self.canvas.main_pm = to_pixmap(img)
            self.canvas.marks = list(marks)
            self.canvas.arrows = list(self.scene.last_arrows)
            self.canvas._arrow_preview = None
            for j in range(len(self.result_details)):
                self._render_inset(j, cursor=False)
        finally:
            QApplication.restoreOverrideCursor()
        self.canvas.update()

    def _render_inset(self, j, cursor=True):
        d = self.result_details[j]
        k = PREVIEW_SCALE
        if cursor:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            img, _ = self.scene.render_step(self.scenario, self.cls, self.step_idx,
                                            (d.rect[2] * lay.IMG_W * k, d.rect[3] * lay.IMG_H * k),
                                            camera=d.camera, scale=1.0, aa=False)
            self.canvas.inset_pm[j] = to_pixmap(img)
        finally:
            if cursor:
                QApplication.restoreOverrideCursor()
        self.canvas.update()

    def _on_changed(self):
        self.canvas.update()

    def _auto(self):
        lay.auto_layout(self.result_details, self.canvas.final_marks())
        self.render_all()

    def _rename(self, j):
        d = self.result_details[j]
        text, ok = QInputDialog.getText(self, "Подпись уточнения", f"Вид {detail_letter(j)}: подпись (можно пустую)",
                                        text=d.title)
        if ok:
            d.title = text.strip()
            self.canvas.update()

    def _menu(self, j, global_pos):
        if j < 0:
            self._rename(-1 - j)
            return
        d = self.result_details[j]
        menu = QMenu(self)
        menu.addAction("Подпись…").triggered.connect(lambda: self._rename(j))
        a = menu.addAction("Обводить место на общем виде")
        a.setCheckable(True)
        a.setChecked(d.mark)
        a.toggled.connect(lambda on: (setattr(d, "mark", on), self.canvas.update()))
        a = menu.addAction("Обводка — снова по виду уточнения")
        a.setToolTip("Отменить ручной сдвиг и размер круга")
        a.setEnabled(d.mark and lay.is_adjusted(d))
        a.triggered.connect(lambda: (setattr(d, "mark_adj", [0.0, 0.0, 1.0]), self.canvas.update()))
        menu.addSeparator()
        menu.addAction("Удалить уточнение").triggered.connect(lambda: self._delete(j))
        theme.popup(menu, global_pos)

    def _arrow_label(self, key):
        node = self.scene.model.by_key.get(key)
        return node.short_label if node else key

    def _arrow_moved(self, i, end, dx, dy):
        a = self.canvas.arrows[i]
        delta = SceneBuilder.frac_to_world(a, end, dx, dy)
        adj = self.result_arrows.setdefault(a["key"], {})
        old = adj.get(end, [0.0, 0.0, 0.0])
        adj[end] = [o + v for o, v in zip(old, delta)]
        self.render_all()

    def _arrow_menu(self, i, global_pos):
        key = self.canvas.arrows[i]["key"]
        menu = QMenu(self)
        a = menu.addAction(f"Стрелка «{self._arrow_label(key)}» — как по умолчанию")
        a.setEnabled(key in self.result_arrows)
        a.triggered.connect(lambda: (self.result_arrows.pop(key, None), self.render_all()))
        theme.popup(menu, global_pos)

    def _reset_arrows(self):
        if self.result_arrows:
            self.result_arrows.clear()
            self.render_all()

    def _delete(self, j):
        del self.result_details[j]
        self.canvas.inset_pm = {}
        self.canvas.current = min(self.canvas.current, len(self.result_details) - 1)
        self.render_all()
