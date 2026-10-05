"""PDF-инструкция по закладке: по странице A4 (альбом) на этап."""
from __future__ import annotations

import os
from datetime import date

from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .model import Classifier, Node
from .scenario import DIRECTIONS, Scenario
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


def export_pdf(path: str, scenario: Scenario, cls: Classifier, scene: SceneBuilder, title: str,
               progress=None):
    _register_fonts()
    W, H = landscape(A4)
    M = 28
    side_w = 240
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

        # картинка
        img_w = W - 2 * M - side_w - 14
        img_h = H - 2 * M - 40
        img = scene.screenshot(scenario, cls, i, size=(int(img_w * 3), int(img_h * 3)))
        c.drawImage(ImageReader(Image.fromarray(img)), M, M + 6, width=img_w, height=img_h,
                    preserveAspectRatio=True, anchor="c")
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.rect(M, M + 6, img_w, img_h)
        c.setStrokeColorRGB(0, 0, 0)

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
        for color, text in ((COLOR_PART_NEW, "закладываемая деталь"),
                            (COLOR_PART_DONE, "уже заложенные детали"),
                            (COLOR_MOVABLE, "прижимы / подвижные элементы"),
                            (COLOR_FIXTURE, "оснастка"),
                            (COLOR_REMOVED, "убрать (полупрозрачно, тёмная стрелка)")):
            ly -= 13
            c.setFillColor(color)
            c.rect(x, ly - 1, 9, 9, stroke=0, fill=1)
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
