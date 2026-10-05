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


@dataclass
class Step:
    name: str = ""
    add: list[str] = field(default_factory=list)    # ключи деталей, закладываемых на этапе
    show: list[str] = field(default_factory=list)   # ключи прижимов/подвижных, видимых на этапе
    direction: str = "-Z"                           # направление закладки (стрелки)
    camera: dict | None = None                      # {position, focal_point, view_up}


@dataclass
class Scenario:
    model: str = ""                                 # путь к .prt (относительно файла сценария)
    fixture_main: str | None = None
    roles: dict[str, str] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)

    def to_dict(self, base_dir: str | None) -> dict:
        model = self.model
        if base_dir and model:
            try:
                model = os.path.relpath(model, base_dir)
            except ValueError:
                pass
        return {
            "model": model.replace("\\", "/"),
            "fixture_main": self.fixture_main,
            "roles": dict(self.roles),
            "steps": [
                {k: v for k, v in {
                    "name": s.name,
                    "add": list(s.add),
                    "show": list(s.show),
                    "direction": s.direction,
                    "camera": s.camera,
                }.items() if v is not None}
                for s in self.steps
            ],
        }

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.to_dict(os.path.dirname(os.path.abspath(path))), f,
                           allow_unicode=True, sort_keys=False, width=200)

    @classmethod
    def load(cls, path: str) -> "Scenario":
        with open(path, encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        model = d.get("model", "")
        if model and not os.path.isabs(model):
            model = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), model))
        return cls(
            model=model,
            fixture_main=d.get("fixture_main"),
            roles=dict(d.get("roles") or {}),
            steps=[Step(name=s.get("name", ""), add=list(s.get("add") or []), show=list(s.get("show") or []),
                        direction=s.get("direction", "-Z"), camera=s.get("camera"))
                   for s in d.get("steps") or []],
        )

    def step_of_part(self, key: str) -> int | None:
        for i, s in enumerate(self.steps):
            if key in s.add:
                return i
        return None
