"""PDF-инструкция по закладке: по странице A4 (альбом) на этап."""
from __future__ import annotations

import hashlib
import os
import sys
from datetime import date

if sys.version_info < (3, 9) and not getattr(hashlib.md5, "_osnastka_compat", False):
    # reportlab 4.1+ вызывает md5(usedforsecurity=False) — параметр появился в Python 3.9;
    # сборка для Windows 7 работает на Python 3.8 (до импорта reportlab)
    _md5 = hashlib.md5

    def _md5_compat(*args, usedforsecurity=True, **kwargs):
        return _md5(*args, **kwargs)

    _md5_compat._osnastka_compat = True
    hashlib.md5 = _md5_compat

from PIL import Image  # noqa: E402
from reportlab.lib.pagesizes import A4, landscape  # noqa: E402
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from . import details as lay
from .model import Classifier, Node
from .scenario import DIRECTIONS, Scenario, detail_letter
from .scene import (COLOR_FIXTURE, COLOR_MOVABLE, COLOR_PART_DONE, COLOR_PART_NEW, COLOR_REMOVED,
                    SceneBuilder)

FONT, FONT_BOLD = "Helvetica", "Helvetica-Bold"
ACCENT = "#D7262D"  # фирменный красный
INK = "#1F2023"


def _register_fonts():
    global FONT, FONT_BOLD
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name, file in (("Arial", "arial.ttf"), ("Arial-Bold", "arialbd.ttf")):
        path = os.path.join(fonts_dir, file)
        if os.path.exists(path) and name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, path))
    if "Arial" in pdfmetrics.getRegisteredFontNames():
        FONT = "Arial"
        FONT_BOLD = "Arial-Bold" if "Arial-Bold" in pdfmetrics.getRegisteredFontNames() else "Arial"


def _node_line(n: Node) -> str:
    return n.label


def draw_step_image(c, scenario: Scenario, cls: Classifier, scene: SceneBuilder, i: int,
                    x0: float, y0: float, img_w: float, img_h: float):
    """Общий вид этапа в прямоугольнике (x0, y0 — левый нижний угол) и окошки уточнений поверх него."""
    step = scenario.steps[i]
    k = lay.RENDER_SCALE
    scale = max(1.0, img_w * k / 1000)
    img, marks = scene.render_step(scenario, cls, i, (img_w * k, img_h * k),
                                   marks=[d.camera for d in step.details], scale=scale)
    c.drawImage(ImageReader(Image.fromarray(img)), x0, y0, width=img_w, height=img_h,
                preserveAspectRatio=True, anchor="c")
    c.setStrokeColorRGB(0.8, 0.8, 0.8)
    c.setLineWidth(1)
    c.rect(x0, y0, img_w, img_h)

    def pt(fx, fy):   # доли картинки (y сверху) -> координаты листа
        return x0 + fx * img_w, y0 + (1 - fy) * img_h

    aspect = img_w / img_h
    marks = [lay.place_mark(m, d, aspect) for d, m in zip(step.details, marks)]
    # обводки мест и выносные линии — под окошками
    for j, (d, m) in enumerate(zip(step.details, marks)):
        if not m:
            continue
        cx, cy = pt(m[0], m[1])
        r = m[2] * img_h
        c.setStrokeColor(lay.CALLOUT)
        c.setLineWidth(1.4)
        c.circle(cx, cy, r, stroke=1, fill=0)
        ln = lay.leader(m, d.rect, aspect)
        if ln:
            c.setLineWidth(0.8)
            c.setDash(3, 2)
            c.line(*pt(*ln[0]), *pt(*ln[1]))
            c.setDash()
        bx, by = pt(*lay.badge_pos(m, [o.rect for o in step.details], aspect))   # буква вида на обводке
        c.setFillColor(lay.CALLOUT)
        c.circle(bx, by, 6.5, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(FONT_BOLD, 8.5)
        c.drawCentredString(bx, by - 3, detail_letter(j))
    for j, d in enumerate(step.details):
        rx, ry_top = pt(d.rect[0], d.rect[1])
        rw, rh = d.rect[2] * img_w, d.rect[3] * img_h
        ry = ry_top - rh
        sub, _ = scene.render_step(scenario, cls, i, (rw * k, rh * k), camera=d.camera, scale=scale)
        c.setFillColorRGB(1, 1, 1)
        c.rect(rx, ry, rw, rh, stroke=0, fill=1)
        c.drawImage(ImageReader(Image.fromarray(sub)), rx, ry, width=rw, height=rh)
        c.setStrokeColor(INK)
        c.setLineWidth(1)
        c.rect(rx, ry, rw, rh, stroke=1, fill=0)
        text = lay.detail_label(j, d)
        lines = simpleSplit(text, FONT_BOLD, 8.5, rw - 10)[:2]
        tab_w = min(rw, max(c.stringWidth(t, FONT_BOLD, 8.5) for t in lines) + 10)
        tab_h = 4 + 10 * len(lines)
        c.setFillColor(lay.CALLOUT)
        c.rect(rx, ry_top - tab_h, tab_w, tab_h, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(FONT_BOLD, 8.5)
        for n, t in enumerate(lines):
            c.drawString(rx + 5, ry_top - 11 - 10 * n, t)
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)
    c.setLineWidth(1)


def export_pdf(path: str, scenario: Scenario, cls: Classifier, scene: SceneBuilder, title: str,
               progress=None):
    _register_fonts()
    W, H = landscape(A4)
    M = lay.MARGIN
    side_w = lay.SIDE_W
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setTitle(title)
    total = len(scenario.steps)
    by_key = scene.model.by_key

    for i, step in enumerate(scenario.steps):
        if progress:
            progress(i, total)
        # заголовок
        c.setFillColor(ACCENT)
        c.rect(M, H - M - 15, 6, 18, stroke=0, fill=1)
        c.setFont(FONT_BOLD, 17)
        stage = "Этап %d из %d" % (i + 1, total)
        c.drawString(M + 14, H - M - 12, stage)
        c.setFillColor(INK)
        if step.name:
            c.drawString(M + 14 + c.stringWidth(stage + "  ", FONT_BOLD, 17), H - M - 12, "— " + step.name)
        c.setFont(FONT, 9)
        c.setFillColorRGB(0.42, 0.44, 0.47)
        c.drawRightString(W - M, H - M - 12, title)
        c.setFillColor(INK)
        c.setStrokeColor(ACCENT)
        c.setLineWidth(1.6)
        c.line(M, H - M - 22, W - M, H - M - 22)
        c.setStrokeColorRGB(0, 0, 0)

        # картинка: общий вид этапа + окошки уточнений
        img_w, img_h = lay.IMG_W, lay.IMG_H
        draw_step_image(c, scenario, cls, scene, i, M, M + 6, img_w, img_h)

        # правая колонка
        x = W - M - side_w
        y = H - M - 44

        def heading(text):
            nonlocal y
            c.setFont(FONT_BOLD, 11)
            c.drawString(x, y, text)
            y -= 15

        def lines(items, marker_color=None, size=9.5):
            nonlocal y
            c.setFont(FONT, size)
            if not items:
                c.setFillColorRGB(0.5, 0.5, 0.5)
                c.drawString(x + 12, y, "—")
                c.setFillColorRGB(0, 0, 0)
                y -= 13
            for text in items:
                wrapped = simpleSplit(text, FONT, size, side_w - 14)
                if marker_color:
                    c.setFillColor(marker_color)
                    c.rect(x + 1, y - 1, 7, 7, stroke=0, fill=1)
                    c.setFillColorRGB(0, 0, 0)
                for j, ln in enumerate(wrapped):
                    c.drawString(x + 12, y, ln)
                    y -= 12
                y -= 2
            y -= 8

        new = [by_key[k] for k in step.add if k in by_key]
        shown = [by_key[k] for k in step.show if k in by_key]
        prev = set(scenario.steps[i - 1].show) if i > 0 else set()
        rem_parts, rem_clamps = scenario.removed_on(i)
        removed = ["Деталь " + _node_line(by_key[k]) for k in rem_parts if k in by_key] + \
                  [_node_line(by_key[k]) for k in rem_clamps if k in by_key]

        heading("Заложить детали:")
        lines([_node_line(n) for n in new], COLOR_PART_NEW)
        if new:
            c.setFont(FONT, 9.5)
            c.drawString(x, y + 4, "Направление закладки: " + DIRECTIONS.get(step.direction, ("—",))[0])
            y -= 14

        heading("Прижимы и подвижные элементы:")
        lines([("+ " if n.key not in prev else "") + _node_line(n) for n in shown], COLOR_MOVABLE)
        if removed:
            heading("Убрать / открыть:")
            lines(removed, COLOR_REMOVED)
            c.setFont(FONT, 8.5)
            c.setFillColorRGB(0.42, 0.44, 0.47)
            c.drawString(x, y + 4, "На картинке полупрозрачно, стрелка — куда убрать")
            c.setFillColorRGB(0, 0, 0)
            y -= 14

        # легенда
        ly = M + 83
        c.setFont(FONT_BOLD, 9)
        c.drawString(x, ly, "Обозначения:")
        if getattr(scenario, "color_mode", "roles") == "nx":
            legend = ((COLOR_PART_NEW, "закладываемая деталь"),
                      (COLOR_MOVABLE, "стрелка — новый прижим этапа"),
                      (None, "остальное — цвета модели NX"),
                      (COLOR_REMOVED, "убрать (полупрозрачно, тёмная стрелка)"))
        else:
            legend = ((COLOR_PART_NEW, "закладываемая деталь"),
                      (COLOR_PART_DONE, "уже заложенные детали"),
                      (COLOR_MOVABLE, "прижимы / подвижные элементы"),
                      (COLOR_FIXTURE, "оснастка"),
                      (COLOR_REMOVED, "убрать (полупрозрачно, тёмная стрелка)"))
        for color, text in legend:
            ly -= 13
            if color:
                c.setFillColor(color)
                c.rect(x, ly - 1, 9, 9, stroke=0, fill=1)
            else:  # «цвета NX» — разноцветный значок
                for k, col in enumerate(("#3d5fca", "#47a523", "#d3837f")):
                    c.setFillColor(col)
                    c.rect(x + k * 3, ly - 1, 3, 9, stroke=0, fill=1)
            c.setFillColorRGB(0, 0, 0)
            c.setFont(FONT, 9)
            c.drawString(x + 14, ly, text)
        c.setFont(FONT, 8)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawString(x, M + 6, "Лист %d / %d · %s" % (i + 1, total, date.today().strftime("%d.%m.%Y")))
        c.setFillColorRGB(0, 0, 0)
        c.showPage()

    if progress:
        progress(total, total)
    c.save()
