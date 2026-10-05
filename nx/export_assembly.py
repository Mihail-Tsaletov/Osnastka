"""NX-журнал: выгружает дерево сборки и сетку каждого компонента.

Запуск: run_journal.exe export_assembly.py -args <сборка.prt> <папка_вывода>

Результат:
  <папка>/model.json   — дерево компонентов
  <папка>/meshes/*.stl — тела каждого компонента в абсолютных координатах сборки (мм)
"""
import json
import os
import sys

import NXOpen
import NXOpen.Assemblies

session = NXOpen.Session.GetSession()
prt_path = os.path.abspath(sys.argv[1])
out_dir = os.path.abspath(sys.argv[2])
mesh_dir = os.path.join(out_dir, "meshes")
os.makedirs(mesh_dir, exist_ok=True)
for f in os.listdir(mesh_dir):
    if f.endswith(".stl"):
        os.remove(os.path.join(mesh_dir, f))

opts = session.Parts.LoadOptions
opts.ComponentsToLoad = NXOpen.LoadOptions.LoadComponents.All
opts.UsePartialLoading = False
opts.UseLightweightRepresentations = False

display_part, load_status = session.Parts.OpenBaseDisplay(prt_path)
load_status.Dispose()

ATTR_DESIGNATION = ("DB_PART_NO", "ОБОЗНАЧЕНИЕ", "Обозначение")
ATTR_TITLE = ("DB_PART_NAME", "НАИМЕНОВАНИЕ", "Наименование")

counter = [0]
warnings = []


def first_attr(obj, titles):
    for t in titles:
        try:
            v = obj.GetStringAttribute(t)
            if v:
                return v
        except Exception:
            pass
    return ""


def export_stl(objects, file_name):
    c = session.DexManager.CreateStlCreator()
    try:
        c.AutoNormalGen = True
        c.ChordalTol = 0.1
        c.AdjacencyTol = 0.1
        c.OutputType = NXOpen.STLCreator.OutputTypeEnum.Binary
        c.OutputFile = os.path.join(mesh_dir, file_name)
        c.ExportSelectionBlock.Add(objects)
        c.Commit()
    finally:
        c.Destroy()
    return file_name if os.path.exists(os.path.join(mesh_dir, file_name)) else None


def solid_bodies(part):
    return [b for b in part.Bodies if b.IsSolidBody or b.IsSheetBody]


def visit(comp, key):
    counter[0] += 1
    node_id = "n%d" % counter[0]
    proto = comp.Prototype if comp is not None else display_part
    part_name = os.path.splitext(os.path.basename(proto.FullPath))[0] if proto is not None else ""
    name = (comp.Name if comp is not None else "") or part_name
    node = {
        "id": node_id,
        "key": key,
        "name": name,
        "part": part_name,
        "designation": (first_attr(comp, ATTR_DESIGNATION) if comp is not None else "") or
                       (first_attr(proto, ATTR_DESIGNATION) if proto is not None else "") or part_name,
        "title": (first_attr(comp, ATTR_TITLE) if comp is not None else "") or
                 (first_attr(proto, ATTR_TITLE) if proto is not None else ""),
        "mesh": None,
        "children": [],
    }

    # собственные тела компонента (у листовых деталей; у сборок обычно пусто)
    if isinstance(proto, NXOpen.Part):
        bodies = solid_bodies(proto)
        if bodies:
            try:
                objs = bodies if comp is None else [comp.FindOccurrence(b) for b in bodies]
                objs = [o for o in objs if o is not None]
                if objs:
                    node["mesh"] = export_stl(objs, node_id + ".stl")
            except Exception as e:
                warnings.append("%s: %s" % (key, e))
    elif proto is None:
        warnings.append("%s: деталь не загружена" % key)

    children = comp.GetChildren() if comp is not None else []
    seen = {}
    for ch in children:
        if ch.IsSuppressed:
            continue
        n = seen.get(ch.Name, 0) + 1
        seen[ch.Name] = n
        ch_key = key + "/" + ch.Name + ("" if n == 1 else "#%d" % n)
        node["children"].append(visit(ch, ch_key))
    return node


root_comp = display_part.ComponentAssembly.RootComponent if isinstance(display_part, NXOpen.Part) else None
if root_comp is not None:
    tree = visit(root_comp, root_comp.Name or os.path.splitext(os.path.basename(prt_path))[0])
    # тела, лежащие прямо в файле верхней сборки
    own = solid_bodies(display_part)
    if own and not tree["mesh"]:
        tree["mesh"] = export_stl(own, tree["id"] + ".stl")
else:
    tree = visit(None, os.path.splitext(os.path.basename(prt_path))[0])

result = {
    "source": prt_path,
    "source_mtime": os.path.getmtime(prt_path),
    "units": "mm",
    "root": tree,
    "warnings": warnings,
}
with open(os.path.join(out_dir, "model.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print("EXPORT_OK", counter[0], "nodes,", len(warnings), "warnings")
for w in warnings:
    print("WARN", w)
