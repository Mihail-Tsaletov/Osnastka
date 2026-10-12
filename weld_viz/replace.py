"""Замена узла (например, «шоп»-детали) деталью или сборкой из отдельного файла NX.

Файл замены выгружается из NX как обычная сборка, совмещается с телами заменяемого узла по геометрии
(align.register), его детали переносятся в координаты сборки и встают в дерево вместо тел узла.
Исходные файлы NX не меняются; замена хранится в сценарии (replacements).
"""
from __future__ import annotations

import os

import numpy as np
import pyvista as pv

from . import nx_bridge
from .align import AlignResult, register
from .model import AssemblyModel
from .scenario import Scenario


def load_replacement(file: str) -> AssemblyModel:
    return AssemblyModel(nx_bridge.export_dir_for(file))


def align(model: AssemblyModel, key: str, repl: AssemblyModel) -> AlignResult:
    """Совмещение файла замены с исходными телами узла key."""
    bodies = [pv.read(n.mesh) for n in model.original_leaves(key) if n.mesh]
    parts = [pv.read(n.mesh) for n in repl.root.walk() if n.mesh]
    return register(parts, bodies)


def apply_all(model: AssemblyModel, scenario: Scenario, export=None) -> list[str]:
    """Применяет замены сценария к модели. export(file) — синхронная выгрузка устаревших файлов (для CLI).

    Возвращает предупреждения (что не удалось применить)."""
    warnings = []
    for key, r in list(scenario.replacements.items()):
        file = r.get("file", "")
        if key not in model.by_key and key not in model.original_keys():
            warnings.append(f"Замена «{os.path.basename(file)}»: узла {key} нет в сборке — пропущена")
            continue
        if not os.path.exists(file):
            warnings.append(f"Файл замены не найден: {file}")
            continue
        if not nx_bridge.is_export_fresh(file):
            if export is None:
                warnings.append(f"Файл замены не выгружен из NX: {file}")
                continue
            export(file)
        repl = load_replacement(file)
        matrix = np.array(r["matrix"], float).reshape(4, 4) if r.get("matrix") else align(model, key, repl).matrix
        model.set_replacement(key, repl, matrix)
    return warnings
