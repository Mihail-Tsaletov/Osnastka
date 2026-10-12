"""Дерево сборки, выгруженное из NX, и классификация компонентов по ролям."""
from __future__ import annotations

import collections
import hashlib
import json
import os
import re
import shutil
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

GROUP_PREFIX = "group:"   # ключ группы тел: <ключ родителя>/group:<имя>

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
    color: int | None = None      # цвет тела в NX (для «шоп»-деталей)
    is_group: bool = False        # группа тел, созданная пользователем
    is_body: bool = False         # отдельное тело многотельной детали
    replaced_by: str | None = None  # узел заменён деталью из этого файла (replace.py)

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
        self.kind = data.get("kind", "assembly")         # assembly | bodies («шоп»-деталь из тел)
        self.missing = data.get("missing", [])           # незагруженные компоненты [{name, count}]
        self.warnings = data.get("warnings", [])
        self.palette = {int(k): tuple(v) for k, v in (data.get("palette") or {}).items()}  # цвет NX -> RGB 0..1
        self.root = self._build(data["root"], None)
        self._orig_nodes = {n.key: n for n in self.root.walk()}
        self._orig_children = {n.key: list(n.children) for n in self.root.walk()}
        self.by_key = {n.key: n for n in self.root.walk()}

    @property
    def missing_count(self) -> int:
        return sum(m["count"] for m in self.missing)

    def _build(self, d: dict, parent: Node | None) -> Node:
        mesh = os.path.join(self.export_dir, "meshes", d["mesh"]) if d.get("mesh") else None
        designation = d["designation"]
        if re.fullmatch(r"\d{1,4}", designation.strip()) and d["part"]:
            designation = d["part"]   # в атрибуте номер позиции, а не обозначение — берём имя файла
        n = Node(d["id"], d["key"], d["name"], d["part"], designation, d.get("title", ""), mesh, parent,
                 color=d.get("color"), is_body=bool(d.get("body")))
        n.children = [self._build(c, n) for c in d["children"]]
        return n

    def apply_groups(self, groups: dict[str, list[str]]):
        """Перестраивает дерево: члены каждой группы переносятся в узел группы у их общего родителя.

        Возвращает список групп, которые не удалось применить (нет членов в модели)."""
        for key, children in self._orig_children.items():
            node = self._orig_nodes[key]
            node.children = list(children)
            for c in node.children:
                c.parent = node
        self.by_key = {n.key: n for n in self.root.walk()}
        skipped = []
        for name, keys in groups.items():
            members = [self.by_key[k] for k in keys if k in self.by_key and not self.by_key[k].is_group]
            if not members:
                skipped.append(name)
                continue
            parent = members[0].parent
            members = [m for m in members if m.parent is parent]
            gkey = parent.key + "/" + GROUP_PREFIX + name
            group = Node("g:" + name, gkey, name, "", name, f"группа, {len(members)} шт.", None, parent,
                         is_group=True)
            group.children = members
            for m in members:
                m.parent = group
            ids = {id(m) for m in members}
            first = min(parent.children.index(m) for m in members)
            rest = [c for c in parent.children if id(c) not in ids]
            parent.children = rest[:first] + [group] + rest[first:]
        self.by_key = {n.key: n for n in self.root.walk()}
        return skipped

    # ------------------------------------------------------------ замена узла деталью из файла
    def original_keys(self) -> set[str]:
        return set(self._orig_nodes)

    def original_leaves(self, key: str) -> list[Node]:
        """Исходные (до замены) узлы с геометрией внутри key."""
        backup = getattr(self, "_replaced_backup", {})
        out, stack = [], list(backup.get(key, self._orig_children.get(key, [])))
        while stack:
            n = stack.pop()
            if n.mesh:
                out.append(n)
            stack.extend(backup.get(n.key, self._orig_children.get(n.key, [])))
        return out

    def set_replacement(self, key: str, repl: "AssemblyModel", matrix):
        """Ставит дерево repl (перенесённое матрицей 4×4 в координаты сборки) вместо содержимого узла key."""
        import numpy as np
        import pyvista as pv

        if not hasattr(self, "_replaced_backup"):
            self._replaced_backup = {}
        node = self._orig_nodes[key]
        if key in self._replaced_backup:
            self.clear_replacement(key)
        matrix = np.asarray(matrix, float).reshape(4, 4)
        out_dir = os.path.join(self.export_dir, "replacements", hashlib.sha1(key.encode("utf-8")).hexdigest()[:12])
        stamp = hashlib.sha1(matrix.round(6).tobytes() + repl.source.encode("utf-8") +
                             str(repl.source_mtime).encode()).hexdigest()
        stamp_file = os.path.join(out_dir, "stamp.txt")
        fresh = os.path.exists(stamp_file) and open(stamp_file, encoding="utf-8").read() == stamp
        if not fresh:
            shutil.rmtree(out_dir, ignore_errors=True)
            os.makedirs(out_dir)

        def graft(rn: Node, parent: Node) -> Node:
            rel = rn.key[len(repl.root.key) + 1:]
            mesh = None
            if rn.mesh:
                mesh = os.path.join(out_dir, rn.id + ".stl")
                if not fresh:
                    pv.read(rn.mesh).transform(matrix, inplace=False).save(mesh)
            n = Node("r" + rn.id + "_" + node.id, key + "/" + rel, rn.name, rn.part, rn.designation, rn.title, mesh,
                     parent, color=rn.color, is_body=rn.is_body)
            n.children = [graft(c, n) for c in rn.children]
            self._orig_nodes[n.key] = n
            self._orig_children[n.key] = list(n.children)
            return n

        self._replaced_backup[key] = self._orig_children[key]
        self._orig_children[key] = [graft(c, node) for c in sorted(repl.root.children, key=lambda c: c.key)]
        if repl.root.mesh:   # тела прямо в файле замены
            self._orig_children[key].append(self._graft_root_mesh(key, node, repl, out_dir, fresh, matrix))
        if not fresh:
            with open(stamp_file, "w", encoding="utf-8") as f:
                f.write(stamp)
        node.replaced_by = repl.source
        self.palette.update(repl.palette)
        node.children = list(self._orig_children[key])
        self.by_key = {n.key: n for n in self.root.walk()}

    def _graft_root_mesh(self, key, node, repl, out_dir, fresh, matrix):
        import pyvista as pv
        mesh = os.path.join(out_dir, "root.stl")
        if not fresh:
            pv.read(repl.root.mesh).transform(matrix, inplace=False).save(mesh)
        n = Node("rroot_" + node.id, key + "/" + repl.root.part, repl.root.name, repl.root.part,
                 repl.root.designation, repl.root.title, mesh, node)
        self._orig_nodes[n.key] = n
        self._orig_children[n.key] = []
        return n

    def clear_replacement(self, key: str):
        backup = getattr(self, "_replaced_backup", {})
        if key not in backup:
            return
        for n in list(self._orig_children[key]):
            for x in n.walk():
                self._orig_nodes.pop(x.key, None)
                self._orig_children.pop(x.key, None)
        self._orig_children[key] = backup.pop(key)
        node = self._orig_nodes[key]
        node.replaced_by = None
        node.children = list(self._orig_children[key])
        self.by_key = {n.key: n for n in self.root.walk()}

    def rgb(self, node: Node) -> tuple[float, float, float] | None:
        return self.palette.get(node.color) if node.color is not None else None

    def guess_fixture_main(self) -> Node | None:
        """Самая крупная подсборка верхнего уровня — скорее всего оснастка."""
        if self.kind == "bodies":
            return None
        # подсборки (не многотельные детали); размер — по числу компонентов, тела не считаются
        candidates = [c for c in self.root.children if any(not x.is_body for x in c.children)]
        if not candidates:
            return None
        return max(candidates, key=lambda c: sum(1 for x in c.walk() if not x.is_body))


def prefix_of(designation: str) -> str:
    """Обозначение изделия без номера позиции.

    0898-8264.000.01            -> 0898-8264
    710.0898-2408.01.00.000-01  -> 710.0898-2408
    DESTACO_247U                -> DESTACO_247U (нет номера вида «цифры-цифры» — до первой точки)
    """
    m = re.match(r"^(.*?\d+-\d+)", designation)
    return m.group(1) if m else designation.split(".", 1)[0]


class Classifier:
    """Вычисляет роль каждого узла: явные назначения пользователя + правила по префиксу."""

    def __init__(self, model: AssemblyModel, fixture_main_key: str | None, overrides: dict[str, str],
                 split=()):
        self.model = model
        self.split = set(split)   # узлы, разобранные на составляющие: строки таблицы — их дети
        self.fixture_main_key = fixture_main_key
        main = model.by_key.get(fixture_main_key) if fixture_main_key else None
        self.prefix = prefix_of(main.designation) if main else ""
        self.overrides = overrides
        self._cache: dict[str, str] = {}

    def default_role(self, node: Node) -> str:
        text = (node.designation + " " + node.title + " " + node.name).lower()
        if self.model.kind == "bodies":
            # «шоп»-деталь: по умолчанию всё — оснастка; прижимы узнаются по имени группы
            return MOVABLE if any(h in text for h in MOVABLE_HINTS) else FIXTURE
        in_fixture = (self.fixture_main_key and
                      (node.key == self.fixture_main_key or node.key.startswith(self.fixture_main_key + "/")))
        if in_fixture or (self.prefix and node.designation.startswith(self.prefix)):
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
            r = self._natural(node, self.role(node.parent))
        self._cache[node.key] = r
        return r

    def _natural(self, node: Node, parent_role: str | None) -> str:
        """Роль без явного назначения пользователя."""
        # члены группы, тела многотельной детали и дети детали/прижима/скрытого наследуют роль родителя
        if node.parent is not None and node.parent.is_group and parent_role is not None:
            return parent_role
        if node.is_body and parent_role is not None:
            return parent_role
        if parent_role in (PART, MOVABLE, HIDDEN):
            return parent_role
        if node.is_group:
            return self._group_role(node, parent_role)
        return self.default_role(node)

    def _group_role(self, group: Node, parent_role: str | None) -> str:
        """Группа по умолчанию получает преобладающую роль своих членов; «прижим» в имени — прижим."""
        if any(h in group.name.lower() for h in MOVABLE_HINTS):
            return MOVABLE
        roles = [self.overrides.get(m.key) or self._natural_member(m, parent_role) for m in group.children]
        if not roles:
            return self.default_role(group)
        counts = collections.Counter(roles)
        priority = [PART, MOVABLE, FIXTURE, HIDDEN]
        return max(counts, key=lambda r: (counts[r], -priority.index(r)))

    def _natural_member(self, m: Node, parent_role: str | None) -> str:
        if m.is_body and parent_role is not None or parent_role in (PART, MOVABLE, HIDDEN):
            return parent_role
        if m.is_group:
            return self._group_role(m, parent_role)
        return self.default_role(m)

    def units(self, role: str) -> list[Node]:
        """Верхние узлы с заданной ролью: строки таблицы этапов (без узлов, у которых нет геометрии)."""
        out = []
        for n in self.model.root.walk():
            if n.parent is None or n.key in self.split or self.role(n) != role:
                continue
            if self.role(n.parent) == role and n.parent.key not in self.split:
                continue   # часть более крупной строки
            if any(x.mesh for x in n.walk()):
                out.append(n)
        return out

    def leaves_with_role(self, role: str) -> list[Node]:
        return [n for n in self.model.root.walk() if n.mesh and self.role(n) == role]
