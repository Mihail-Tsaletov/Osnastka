"""NX-журнал: выгружает дерево сборки (или тела «шоп»-детали) и сетку каждого узла.

Запуск: run_journal.exe export_assembly.py -args <файл.prt> <папка_вывода>

Два вида файлов:
  * сборка — узлы = компоненты. Компоненты ищутся в папке файла и её подпапках (не на сервере);
    те, что не нашлись (стандартные и покупные изделия из библиотеки), пропускаются и
    перечисляются в "missing";
  * одна деталь с несколькими телами («шоп»-файл: оснастка и деталь собраны в одной детали) —
    узлы = твёрдые тела.

Результат:
  <папка>/model.json   — дерево узлов
  <папка>/meshes/*.stl — геометрия каждого узла в абсолютных координатах (мм)
"""
import collections
import json
import os
import re
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
opts.AbortOnFailure = False
# искать компоненты в папке файла и подпапках; недоступные сетевые библиотеки не опрашиваются
opts.ComponentLoadMethod = NXOpen.LoadOptions.LoadMethod.SearchDirectories
opts.SetSearchDirectories([os.path.dirname(prt_path)], [True])

display_part, load_status = session.Parts.OpenBaseDisplay(prt_path)
load_status.Dispose()

ATTR_DESIGNATION = ("DB_PART_NO", "ОБОЗНАЧЕНИЕ", "Обозначение")
ATTR_TITLE = ("DB_PART_NAME", "НАИМЕНОВАНИЕ", "Наименование")

counter = [0]
warnings = []
missing = collections.Counter()   # имя незагруженного компонента -> количество


def new_id():
    counter[0] += 1
    return "n%d" % counter[0]


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


def part_name_of(part):
    return os.path.splitext(os.path.basename(part.FullPath))[0]


# ------------------------------------------------------------------ сборка
def visit(comp, key):
    node_id = new_id()
    proto = comp.Prototype
    part_name = part_name_of(proto)
    node = {
        "id": node_id,
        "key": key,
        "name": comp.Name or part_name,
        "part": part_name,
        "designation": first_attr(comp, ATTR_DESIGNATION) or first_attr(proto, ATTR_DESIGNATION) or part_name,
        "title": first_attr(comp, ATTR_TITLE) or first_attr(proto, ATTR_TITLE),
        "mesh": None,
        "children": [],
    }
    bodies = solid_bodies(proto)
    solids = [b for b in bodies if b.IsSolidBody]
    loaded_children = [ch for ch in comp.GetChildren() if isinstance(ch.Prototype, NXOpen.Part)]
    if len(solids) >= 2 and not loaded_children:
        # многотельная деталь («шоп»-узел внутри сборки): каждое тело — отдельный узел,
        # чтобы узел можно было разобрать на детали и закладывать их по очереди
        node["multibody"] = True
        used = set()
        for i, b in enumerate(solids):
            tag = body_tag(b, i)
            while tag in used:
                tag += "_"
            used.add(tag)
            child_id = new_id()
            mesh = None
            try:
                occ = comp.FindOccurrence(b)
                if occ is not None:
                    mesh = export_stl([occ], child_id + ".stl")
            except Exception as e:
                warnings.append("%s/%s: %s" % (key, tag, e))
            node["children"].append({
                "id": child_id, "key": key + "/" + tag, "name": b.Name or tag, "part": "",
                "designation": b.Name or tag, "title": "", "mesh": mesh, "color": b.Color, "body": True,
                "children": [],
            })
    elif bodies:  # собственные тела компонента (у листовых деталей; у сборок обычно пусто)
        try:
            objs = [o for o in (comp.FindOccurrence(b) for b in bodies) if o is not None]
            if objs:
                node["mesh"] = export_stl(objs, node_id + ".stl")
        except Exception as e:
            warnings.append("%s: %s" % (key, e))

    seen = {}
    for ch in comp.GetChildren():
        if ch.IsSuppressed:
            continue
        if not isinstance(ch.Prototype, NXOpen.Part):  # файл не найден — пропускаем
            missing[ch.DisplayName or ch.Name] += 1
            continue
        n = seen.get(ch.Name, 0) + 1
        seen[ch.Name] = n
        ch_key = key + "/" + ch.Name + ("" if n == 1 else "#%d" % n)
        node["children"].append(visit(ch, ch_key))
    return node


# ------------------------------------------------------------------ деталь из тел
def body_tag(body, index):
    """Устойчивое имя тела: имя BREP-фичи «Body(12)» → «Body12»; иначе порядковый номер."""
    try:
        feats = body.GetFeatures()
        if feats:
            m = re.search(r"\d+", feats[0].GetFeatureName())
            if m:
                return "Body%s" % m.group(0)
    except Exception:
        pass
    return "Body%d" % index


def visit_bodies(part, key):
    root = {
        "id": new_id(), "key": key, "name": part_name_of(part), "part": part_name_of(part),
        "designation": first_attr(part, ATTR_DESIGNATION) or part_name_of(part),
        "title": first_attr(part, ATTR_TITLE), "mesh": None, "children": [],
    }
    used = set()
    for i, b in enumerate(b for b in part.Bodies if b.IsSolidBody):
        tag = body_tag(b, i)
        while tag in used:
            tag += "_"
        used.add(tag)
        node_id = new_id()
        try:
            mesh = export_stl([b], node_id + ".stl")
        except Exception as e:
            warnings.append("%s: %s" % (tag, e))
            mesh = None
        root["children"].append({
            "id": node_id, "key": key + "/" + tag, "name": b.Name or tag, "part": "",
            "designation": b.Name or tag, "title": "", "mesh": mesh, "color": b.Color, "body": True,
            "children": [],
        })
    return root


stem = os.path.splitext(os.path.basename(prt_path))[0]
root_comp = display_part.ComponentAssembly.RootComponent if isinstance(display_part, NXOpen.Part) else None
if root_comp is not None and list(root_comp.GetChildren()):
    kind = "assembly"
    tree = visit(root_comp, root_comp.Name or stem)
    own = solid_bodies(display_part)  # тела, лежащие прямо в файле верхней сборки
    if own and not tree["mesh"]:
        tree["mesh"] = export_stl(own, tree["id"] + ".stl")
else:
    kind = "bodies"
    tree = visit_bodies(display_part, stem)

# палитра NX: номер цвета -> RGB (0..1) для цветов, встречающихся у тел
palette = {}
try:
    import NXOpen.UF
    uf = NXOpen.UF.UFSession.GetUFSession()
    def all_nodes(n):
        yield n
        for c in n["children"]:
            yield from all_nodes(c)

    for idx in sorted({n.get("color") for n in all_nodes(tree) if n.get("color") is not None}):
        values = uf.Disp.AskColor(idx, 0)[1]   # 0 — модель RGB
        palette[str(idx)] = [round(float(v), 4) for v in values]
except Exception as e:
    warnings.append("палитра цветов: %s" % e)

result = {
    "format": 2,   # nx_bridge.EXPORT_FORMAT: при изменении выгрузки старые кэши пересоздаются
    "source": prt_path,
    "source_mtime": os.path.getmtime(prt_path),
    "units": "mm",
    "kind": kind,
    "root": tree,
    "missing": [{"name": k, "count": v} for k, v in missing.most_common()],
    "palette": palette,
    "warnings": warnings,
}
with open(os.path.join(out_dir, "model.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=1)
print("EXPORT_OK", kind, counter[0], "nodes,", sum(missing.values()), "missing,", len(warnings), "warnings")
for w in warnings[:50]:
    print("WARN", w)
