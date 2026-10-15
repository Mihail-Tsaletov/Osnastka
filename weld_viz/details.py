"""Компоновка листа этапа: общий вид + окошки уточнений. Общая геометрия для PDF и окна компоновки."""
from __future__ import annotations

import math

from .scenario import Detail, clamp_rect, detail_letter

# лист PDF: A4 альбомный, пункты
PAGE_W, PAGE_H = 841.89, 595.28
MARGIN = 28
SIDE_W = 240                       # правая колонка со списками
IMG_X = MARGIN
IMG_TOP = MARGIN + 34              # верх картинки этапа от верха листа
IMG_W = PAGE_W - 2 * MARGIN - SIDE_W - 14
IMG_H = PAGE_H - 2 * MARGIN - 40
RENDER_SCALE = 3                   # пикселей снимка на пункт листа
CALLOUT = "#1565C0"                # обводка места, выноска и подпись уточнения — синие, не путать с деталями
MIN_MARK_R = 0.012                 # самая маленькая обводка, доля высоты картинки

DEFAULT_SIZE = (0.36, 0.40)
CORNERS = ((1, 1), (1, 0), (0, 1), (0, 0))   # правый нижний, правый верхний, левый нижний, левый верхний
INSET_GAP = 0.015


def detail_label(i: int, d: Detail) -> str:
    """Подпись окошка: «Вид А» или «Вид А: подпись»."""
    return f"Вид {detail_letter(i)}" + (f": {d.title}" if d.title else "")


def corner_rect(corner, size=DEFAULT_SIZE) -> list[float]:
    w, h = size
    cx, cy = corner
    return [INSET_GAP if cx == 0 else 1 - w - INSET_GAP, INSET_GAP if cy == 0 else 1 - h - INSET_GAP, w, h]


def _overlap(a, b) -> float:
    dx = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    dy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(dx, 0) * max(dy, 0)


def free_rect(others: list[list[float]], mark=None) -> list[float]:
    """Место для нового окошка: свободный угол картинки, по возможности подальше от обведённого места."""
    best, best_score = None, None
    for corner in CORNERS:
        r = corner_rect(corner)
        score = sum(_overlap(r, o) for o in others) * 100
        if mark:   # окошко не должно закрывать то, что уточняет
            cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
            score -= math.hypot(cx - mark[0], cy - mark[1])
        if best_score is None or score < best_score - 1e-9:
            best, best_score = r, score
    return clamp_rect(best)


def auto_layout(details: list[Detail], marks) -> None:
    """Расставить окошки по углам картинки (marks — места уточнений на общем виде или None)."""
    placed = []
    for d, m in zip(details, marks):
        size = (d.rect[2], d.rect[3])
        best, best_score = None, None
        for corner in CORNERS:
            r = clamp_rect(corner_rect(corner, size))
            score = sum(_overlap(r, o) for o in placed) * 100
            if m:
                score -= math.hypot(r[0] + r[2] / 2 - m[0], r[1] + r[3] / 2 - m[1])
            if best_score is None or score < best_score - 1e-9:
                best, best_score = r, score
        d.rect = best
        placed.append(best)


def leader(mark, rect, aspect: float):
    """Выносная линия от обводки места к окошку: ((x1, y1), (x2, y2)) в долях картинки или None.

    aspect — ширина / высота картинки (радиус обводки — доля высоты)."""
    if not mark:
        return None
    mx, my, r = mark
    rx, ry, rw, rh = rect
    if rx <= mx <= rx + rw and ry <= my <= ry + rh:
        return None   # место под окошком
    tx, ty = rx + rw / 2, ry + rh / 2
    # направление в «квадратных» единицах (по высоте), чтобы окружность была окружностью
    dx, dy = (tx - mx) * aspect, ty - my
    dist = math.hypot(dx, dy)
    if dist < 1e-9:
        return None
    ux, uy = dx / dist, dy / dist
    start = (mx + ux * r / aspect, my + uy * r)
    # пересечение отрезка центр места → центр окошка с границей окошка
    t_best = 1.0
    for edge_x in (rx, rx + rw):
        if abs(tx - mx) > 1e-12:
            t = (edge_x - mx) / (tx - mx)
            y = my + t * (ty - my)
            if 0 <= t <= 1 and ry - 1e-9 <= y <= ry + rh + 1e-9:
                t_best = min(t_best, t)
    for edge_y in (ry, ry + rh):
        if abs(ty - my) > 1e-12:
            t = (edge_y - my) / (ty - my)
            x = mx + t * (tx - mx)
            if 0 <= t <= 1 and rx - 1e-9 <= x <= rx + rw + 1e-9:
                t_best = min(t_best, t)
    end = (mx + (tx - mx) * t_best, my + (ty - my) * t_best)
    if math.hypot((end[0] - start[0]) * aspect, end[1] - start[1]) < 0.01 or \
            math.hypot((end[0] - mx) * aspect, end[1] - my) <= r:
        return None   # окошко вплотную к обводке
    return start, end


def visible_mark(mark, max_r: float = 0.42):
    """Обводка имеет смысл только для крупного вида: место меньше половины кадра."""
    if not mark or mark[2] <= 0 or mark[2] > max_r:
        return None
    return mark


def is_adjusted(d: Detail) -> bool:
    return [round(float(v), 4) for v in d.mark_adj] != [0.0, 0.0, 1.0]


def place_mark(raw, d: Detail, aspect: float):
    """Обводка уточнения на общем виде (x, y, r) с учётом ручной правки или None.

    raw — место по ракурсу уточнения (SceneBuilder.render_step), aspect — ширина / высота картинки."""
    if not raw or not d.mark or raw[2] <= 1e-6:
        return None
    if not is_adjusted(d):
        return visible_mark(raw)
    dx, dy, k = d.mark_adj
    return raw[0] + dx * raw[2] / aspect, raw[1] + dy * raw[2], max(raw[2] * k, MIN_MARK_R)


def badge_pos(mark, rects, aspect: float) -> tuple[float, float]:
    """Где на обводке поставить букву вида: справа сверху, а если там окошко — в другом свободном месте."""
    x, y, r = mark
    pts = []
    for deg in (45, 135, -45, -135, 90, 0, 180, -90):
        a = math.radians(deg)
        pts.append((x + r * math.cos(a) / aspect, y - r * math.sin(a)))
    pad = 0.015
    for px, py in pts:
        if not (0 <= px <= 1 and 0 <= py <= 1):
            continue
        if any(rx - pad <= px <= rx + rw + pad and ry - pad <= py <= ry + rh + pad for rx, ry, rw, rh in rects):
            continue
        return px, py
    return pts[0]


def adjust_for(raw, x: float, y: float, r: float, aspect: float) -> list[float]:
    """Ручная правка, при которой обводка встанет в (x, y) с радиусом r."""
    r = max(r, MIN_MARK_R)
    return [(x - raw[0]) * aspect / raw[2], (y - raw[1]) / raw[2], r / raw[2]]
