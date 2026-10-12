"""NX-журнал (для тестов): делает «нормальную» сборку узла из «шоп»-детали.

Запуск: run_journal.exe build_replacement_test.py -args <шоп.prt> <папка_вывода>

Каждое крупное тело шоп-детали выносится в отдельный .prt с атрибутами обозначения и наименования,
из них собираются две сборки:
  <имя>_ASM.prt        — в той же системе координат, что и шоп-деталь;
  <имя>_ASM_MOVED.prt  — та же сборка, повёрнутая на 90° вокруг Z и сдвинутая (проверка совмещения по геометрии).
"""
import math
import os
import sys

import NXOpen
import NXOpen.UF

session = NXOpen.Session.GetSession()
uf = NXOpen.UF.UFSession.GetUFSession()
shop_path = os.path.abspath(sys.argv[1])
out_dir = os.path.abspath(sys.argv[2])
os.makedirs(out_dir, exist_ok=True)
stem = os.path.splitext(os.path.basename(shop_path))[0].split("_")[0]   # S300.14.02.010A
log = open(os.path.join(out_dir, "build_log.txt"), "w", encoding="utf-8")

shop, st = session.Parts.OpenBaseDisplay(shop_path)
st.Dispose()


def box(b):
    r = uf.ModlGeneral.AskBoundingBox(b.Tag)
    return [r[3] - r[0], r[4] - r[1], r[5] - r[2]]


bodies = [b for b in shop.Bodies if b.IsSolidBody and b.Color != 216]   # 216 — сварные швы
bodies.sort(key=lambda b: -max(box(b)))
log.write(f"bodies to export: {len(bodies)}\n")

NAMES = [(1100, "Лонжерон"), (600, "Поперечина"), (150, "Стойка"), (0, "Косынка")]
parts = []
for i, b in enumerate(bodies, 1):
    designation = f"{stem}.{i:03d}"
    name = next(n for limit, n in NAMES if max(box(b)) >= limit)
    path = os.path.join(out_dir, designation + ".prt")
    if os.path.exists(path):
        os.remove(path)
    uf.Part.Export(path, 1, [b.Tag])
    parts.append((path, designation, name))
    log.write(f"  {designation} {name} size={[round(v) for v in box(b)]} color={b.Color}\n")

# атрибуты деталей
for path, designation, name in parts:
    p, st = session.Parts.Open(path)
    st.Dispose()
    p.SetUserAttribute("DB_PART_NO", -1, designation, NXOpen.Update.Option.Now)
    p.SetUserAttribute("DB_PART_NAME", -1, name, NXOpen.Update.Option.Now)
    p.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.TrueValue)


def matrix(deg):
    c, s = round(math.cos(math.radians(deg)), 9), round(math.sin(math.radians(deg)), 9)
    m = NXOpen.Matrix3x3()
    m.Xx, m.Xy, m.Xz = c, s, 0.0
    m.Yx, m.Yy, m.Yz = -s, c, 0.0
    m.Zx, m.Zy, m.Zz = 0.0, 0.0, 1.0
    return m


def make_asm(name, deg, offset):
    path = os.path.join(out_dir, name + ".prt")
    if os.path.exists(path):
        os.remove(path)
    asm = session.Parts.NewDisplay(path, NXOpen.Part.Units.Millimeters)
    for ppath, designation, _ in parts:
        asm.ComponentAssembly.AddComponent(ppath, "Entire Part", designation,
                                           NXOpen.Point3d(*map(float, offset)), matrix(deg), -1)
    asm.SetUserAttribute("DB_PART_NO", -1, stem, NXOpen.Update.Option.Now)
    asm.SetUserAttribute("DB_PART_NAME", -1, "Рама (сборка)", NXOpen.Update.Option.Now)
    asm.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.TrueValue)
    log.write(f"assembly: {path}\n")


make_asm(stem + "_ASM", 0, (0, 0, 0))
make_asm(stem + "_ASM_MOVED", 90, (500, -200, 100))
log.close()
print("BUILD_OK", len(parts))
