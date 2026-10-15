"""Пакетная генерация PDF по готовому сценарию.

python -m weld_viz.cli сценарий.yaml [-o out.pdf] [--reexport]
"""
from __future__ import annotations

import argparse
import os

from . import nx_bridge, nx_link, replace
from .model import AssemblyModel, Classifier
from .pdf_report import export_pdf
from .scenario import Scenario
from .scene import SceneBuilder


def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF-инструкция закладки по сценарию")
    ap.add_argument("scenario")
    ap.add_argument("-o", "--output")
    ap.add_argument("--reexport", action="store_true", help="заново выгрузить сборку из NX")
    a = ap.parse_args(argv)

    sc = Scenario.load(a.scenario)
    if a.reexport or not nx_bridge.is_export_fresh(sc.model):
        print("Экспорт из NX:", sc.model)
        nx_bridge.run_export(sc.model)
    model = AssemblyModel(nx_bridge.export_dir_for(sc.model))
    for w in replace.apply_all(model, sc, export=nx_bridge.run_export):
        print("!", w)
    model.apply_groups(sc.groups)
    cls = Classifier(model, sc.fixture_main, sc.roles, sc.split)
    out = a.output or os.path.splitext(a.scenario)[0] + ".pdf"
    export_pdf(out, sc, cls, SceneBuilder(model), title=nx_link.model_title(sc.model),
               progress=lambda i, n: print(f"  этап {i}/{n}"))
    print("PDF:", out)


if __name__ == "__main__":
    main()
