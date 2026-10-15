"""Архив обновления кода: Osnastka-update-<версия>.zip (для программы, установленной без прав на запись).

  python tools/make_update.py [папка_вывода] [--notes "что нового"]

Внутри: weld_viz/*.py, nx/osnastka_nx.cs, update.json {version, runtime, notes}. Версия — из
weld_viz/__init__.py, runtime — из osnastka_build.py. Архив один для сборок Windows 10/11 и Windows 7.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_const(path: str, name: str) -> str:
    with open(path, encoding="utf-8") as f:
        m = re.search(rf'^{name}\s*=\s*"?([^"\n]+)"?', f.read(), re.M)
    if not m:
        sys.exit(f"Нет {name} в {path}")
    return m.group(1).strip()


def make_update(out_dir: str, notes: str = "") -> str:
    version = read_const(os.path.join(ROOT, "weld_viz", "__init__.py"), "__version__")
    runtime = int(read_const(os.path.join(ROOT, "osnastka_build.py"), "RUNTIME"))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"Osnastka-update-{version}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("update.json", json.dumps({"version": version, "runtime": runtime, "notes": notes},
                                             ensure_ascii=False, indent=1))
        pkg = os.path.join(ROOT, "weld_viz")
        for name in sorted(os.listdir(pkg)):
            if name.endswith(".py"):
                z.write(os.path.join(pkg, name), "weld_viz/" + name)
        z.write(os.path.join(ROOT, "nx", "osnastka_nx.cs"), "nx/osnastka_nx.cs")
    print("Архив обновления:", path, f"{os.path.getsize(path) / 1024:.0f} КБ", f"(версия {version}, runtime {runtime})")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Архив обновления кода Osnastka")
    ap.add_argument("out", nargs="?", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    make_update(a.out, a.notes)
