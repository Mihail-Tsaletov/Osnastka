"""Дерево сборки, выгруженное из NX, и классификация компонентов по ролям."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

# Роли компонентов
FIXTURE = "fixture"   # оснастка: видна на всех этапах
MOVABLE = "movable"   # прижим / палец / упор: виден только на отмеченных этапах
PART = "part"         # свариваемая деталь: закладывается на одном из этапов
HIDDEN = "hidden"     # не показывать никогда

ROLE_TITLES = {
    FIXTURE: "Оснастка",
    MOVABLE: "Прижим / подвижный",
    PART: "Деталь",
    HIDDEN: "Скрыть",
}

# Подсказки для автоопределения подвижных элементов оснастки
MOVABLE_HINTS = ("прижим", "clamp", "destaco", "палец", "фиксатор", "pin", "цилиндр", "cylinder")


@dataclass
class Node:
    id: str
    key: str
    name: str
    part: str
    designation: str
    title: str
    mesh: str | None
    parent: "Node | None" = None
    children: list["Node"] = field(default_factory=list)

    @property
    def short_label(self) -> str:
        """Обозначение с номером экземпляра: 2210-0035.002 (2)"""
        last = self.key.rsplit("/", 1)[-1]
        return self.designation + (" (" + last.rsplit("#", 1)[1] + ")" if "#" in last else "")

    @property
    def label(self) -> str:
        return self.short_label + (" " + self.title if self.title else "")

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def mesh_files(self) -> list[str]:
        return [n.mesh for n in self.walk() if n.mesh]


class AssemblyModel:
    def __init__(self, export_dir: str):
        self.export_dir = export_dir
        with open(os.path.join(export_dir, "model.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.source = data["source"]
        self.source_mtime = data.get("source_mtime", 0)
        self.warnings = data.get("warnings", [])
        self.root = self._build(data["root"], None)
        self.by_key = {n.key: n for n in self.root.walk()}

    def _build(self, d: dict, parent: Node | None) -> Node:
        mesh = os.path.join(self.export_dir, "meshes", d["mesh"]) if d.get("mesh") else None
        n = Node(d["id"], d["key"], d["name"], d["part"], d["designation"], d.get("title", ""), mesh, parent)
        n.children = [self._build(c, n) for c in d["children"]]
        return n

    def guess_fixture_main(self) -> Node | None:
        """Самая крупная подсборка верхнего уровня — скорее всего оснастка."""
        candidates = [c for c in self.root.children if c.children]
        if not candidates:
            return None
        return max(candidates, key=lambda c: sum(1 for _ in c.walk()))


def prefix_of(designation: str) -> str:
    """0898-8264.000.01 -> 0898-8264"""
    return designation.split(".", 1)[0]


class Classifier:
    """Вычисляет роль каждого узла: явные назначения пользователя + правила по префиксу."""

    def __init__(self, model: AssemblyModel, fixture_main_key: str | None, overrides: dict[str, str]):
        self.model = model
        self.fixture_main_key = fixture_main_key
        main = model.by_key.get(fixture_main_key) if fixture_main_key else None
        self.prefix = prefix_of(main.designation) if main else ""
        self.overrides = overrides
        self._cache: dict[str, str] = {}

    def default_role(self, node: Node) -> str:
        in_fixture = (self.fixture_main_key and
                      (node.key == self.fixture_main_key or node.key.startswith(self.fixture_main_key + "/")))
        if in_fixture or (self.prefix and node.designation.startswith(self.prefix)):
            text = (node.designation + " " + node.title + " " + node.name).lower()
            if any(h in text for h in MOVABLE_HINTS):
                return MOVABLE
            return FIXTURE
        return PART

    def role(self, node: Node) -> str | None:
        """None — для корня сборки (у него нет роли)."""
        if node.parent is None:
            return None
        if node.key in self._cache:
            return self._cache[node.key]
        r = self.overrides.get(node.key)
        if r is None:
            parent_role = self.role(node.parent)
            # дети детали/прижима/скрытого наследуют роль; внутри оснастки — правила
            if parent_role in (PART, MOVABLE, HIDDEN):
                r = parent_role
            else:
                r = self.default_role(node)
        self._cache[node.key] = r
        return r

    def units(self, role: str) -> list[Node]:
        """Верхние узлы с заданной ролью: строки таблицы этапов."""
        out = []
        for n in self.model.root.walk():
            if n.parent is not None and self.role(n) == role and self.role(n.parent) != role:
                out.append(n)
        return out

    def leaves_with_role(self, role: str) -> list[Node]:
        return [n for n in self.model.root.walk() if n.mesh and self.role(n) == role]
