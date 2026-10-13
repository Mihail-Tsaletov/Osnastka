"""Сценарий закладки: этапы, роли компонентов, ракурсы. Хранится в YAML."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

DIRECTIONS = {
    "-Z": ("сверху", (0, 0, -1)),
    "+Z": ("снизу", (0, 0, 1)),
    "-X": ("справа (+X → −X)", (-1, 0, 0)),
    "+X": ("слева (−X → +X)", (1, 0, 0)),
    "-Y": ("сзади (+Y → −Y)", (0, -1, 0)),
    "+Y": ("спереди (−Y → +Y)", (0, 1, 0)),
}


def find_for_model(source: str) -> list[tuple[str, bool]]:
    """Сценарии к модели: [(путь, та_же_модель)]. Сначала — с тем же полным путём к модели, затем — с тем же
    именем файла модели (модель перенесли или открыли копию). Ищутся в папке сценариев и рядом с моделью."""
    from . import nx_link, paths

    def model_of(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
            m = d.get("model", "")
            if m and not os.path.isabs(m) and not m.startswith("tc:"):
                m = os.path.normpath(os.path.join(os.path.dirname(path), m))
            return m
        except Exception:
            return None

    files = []
    folder = paths.scenarios_dir()
    files += [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(paths.SCENARIO_EXT)]
    if not nx_link.is_tc(source):
        legacy = os.path.splitext(source)[0] + paths.SCENARIO_EXT   # версии до 0.4 — рядом с моделью
        if os.path.exists(legacy):
            files.append(legacy)
    same_path = paths.norm(source) if not nx_link.is_tc(source) else source
    stem = nx_link.model_stem(source).lower()
    found = []
    for path in files:
        m = model_of(path)
        if not m:
            continue
        exact = (m == source) if nx_link.is_tc(source) else (not m.startswith("tc:") and paths.norm(m) == same_path)
        if exact or nx_link.model_stem(m).lower() == stem:
            found.append((path, exact))
    found.sort(key=lambda x: (not x[1], -os.path.getmtime(x[0])))
    return found


@dataclass
class Step:
    name: str = ""
    add: list[str] = field(default_factory=list)    # ключи деталей, закладываемых на этапе
    show: list[str] = field(default_factory=list)   # ключи прижимов/подвижных, видимых на этапе
    hide: list[str] = field(default_factory=list)   # ранее заложенные детали, скрытые на этом этапе
    direction: str = "-Z"                           # направление закладки (стрелки)
    camera: dict | None = None                      # {position, focal_point, view_up}


@dataclass
class Scenario:
    model: str = ""                                 # путь к .prt (относительно файла сценария)
    fixture_main: str | None = None
    roles: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)   # имя группы -> ключи тел/компонентов
    split: list[str] = field(default_factory=list)   # узлы, разобранные на составляющие
    # узел -> {"file": .prt замены, "matrix": 16 чисел (файл -> сборка), "summary": итог совмещения}
    replacements: dict[str, dict] = field(default_factory=dict)
    color_mode: str = "roles"   # roles — цвета по ролям; nx — оснастка и детали цветами из NX
    steps: list[Step] = field(default_factory=list)

    def to_dict(self, base_dir: str | None) -> dict:
        model = self.model
        if base_dir and model and not model.startswith("tc:"):
            try:
                model = os.path.relpath(model, base_dir)
            except ValueError:
                pass
        return {
            "model": model.replace("\\", "/"),
            "fixture_main": self.fixture_main,
            "roles": dict(self.roles),
            "groups": {k: list(v) for k, v in self.groups.items()},
            "split": list(self.split),
            "color_mode": self.color_mode,
            "replacements": {k: dict(v, file=self._rel(v.get("file", ""), base_dir))
                             for k, v in self.replacements.items()},
            "steps": [
                {k: v for k, v in {
                    "name": s.name,
                    "add": list(s.add),
                    "show": list(s.show),
                    "hide": list(s.hide) or None,
                    "direction": s.direction,
                    "camera": s.camera,
                }.items() if v is not None}
                for s in self.steps
            ],
        }

    @staticmethod
    def _rel(file: str, base_dir: str | None) -> str:
        if base_dir and file:
            try:
                file = os.path.relpath(file, base_dir)
            except ValueError:
                pass
        return file.replace("\\", "/")

    @staticmethod
    def _abs(file: str, scenario_path: str) -> str:
        if file and not os.path.isabs(file):
            file = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(scenario_path)), file))
        return file

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.to_dict(os.path.dirname(os.path.abspath(path))), f,
                           allow_unicode=True, sort_keys=False, width=200)

    @classmethod
    def load(cls, path: str) -> "Scenario":
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        model = d.get("model", "")
        if model and not os.path.isabs(model) and not model.startswith("tc:"):
            model = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), model))
        return cls(
            model=model,
            fixture_main=d.get("fixture_main"),
            roles=dict(d.get("roles") or {}),
            groups={k: list(v) for k, v in (d.get("groups") or {}).items()},
            split=list(d.get("split") or []),
            color_mode=d.get("color_mode", "roles"),
            replacements={k: dict(v, file=cls._abs(v.get("file", ""), path))
                          for k, v in (d.get("replacements") or {}).items()},
            steps=[Step(name=s.get("name", ""), add=list(s.get("add") or []), show=list(s.get("show") or []),
                        hide=list(s.get("hide") or []),
                        direction=s.get("direction", "-Z"), camera=s.get("camera"))
                   for s in d.get("steps") or []],
        )

    def removed_on(self, i: int) -> tuple[list[str], list[str]]:
        """Что убирается на этапе i по сравнению с предыдущим: (детали, прижимы)."""
        if i <= 0 or i >= len(self.steps):
            return [], []
        prev, cur = self.steps[i - 1], self.steps[i]
        before_prev = [k for s in self.steps[:i - 1] for k in s.add]
        prev_visible = [k for k in before_prev if k not in prev.hide] + list(prev.add)
        parts = [k for k in prev_visible if k in cur.hide]
        clamps = [k for k in prev.show if k not in cur.show]
        return parts, clamps

    def step_of_part(self, key: str) -> int | None:
        for i, s in enumerate(self.steps):
            if key in s.add:
                return i
        return None
