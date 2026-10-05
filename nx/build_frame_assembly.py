"""NX-журнал: тестовая сборка «Рама на оснастке» (блоки, цилиндры, повёрнутые прижимы, атрибуты).

Запуск: run_journal.exe build_frame_assembly.py -args <папка_вывода>

Структура:
  FRAME_OPERATION.prt
    0912-4410.000.01  Оснастка сборочно-сварочная (подсборка)
      0912-4410.001  Плита основания            x1
      0912-4410.002  Опора                      x6
      0912-4410.003  Упор угловой (2 тела)      x4
      0912-4410.004  Палец фиксирующий (цил.)   x2
      0912-4410.100  Прижим вертикальный (подсборка: .101 корпус, .102 рычаг)  x6
      PNEUMO_DCL40   Пневмоприжим (покупной, одно тело, без префикса)          x2
    2210-0035.001  Лонжерон              x2
    2210-0035.002  Поперечина торцевая   x2
    2210-0035.003  Поперечина средняя    x1
    2210-0035.004  Косынка               x4
    2210-0035.005  Проушина              x2
    2210-0035.010  Кронштейн (подсборка: .011 полка, .012 ребро)
"""
import math
import os
import sys

import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities

session = NXOpen.Session.GetSession()
out_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                          os.path.join(os.path.dirname(__file__), "..", "test_frame"))
os.makedirs(out_dir, exist_ok=True)
UPD = NXOpen.SmartObject.UpdateOption.WithinModeling


def rot_z(deg):
    c, s = round(math.cos(math.radians(deg)), 9), round(math.sin(math.radians(deg)), 9)
    m = NXOpen.Matrix3x3()
    m.Xx, m.Xy, m.Xz = c, s, 0.0
    m.Yx, m.Yy, m.Yz = -s, c, 0.0
    m.Zx, m.Zy, m.Zz = 0.0, 0.0, 1.0
    return m


def path_of(name):
    return os.path.join(out_dir, name + ".prt")


def P(x, y, z):
    return NXOpen.Point3d(float(x), float(y), float(z))


def new_part(name):
    p = path_of(name)
    if os.path.exists(p):
        os.remove(p)
    return session.Parts.NewDisplay(p, NXOpen.Part.Units.Millimeters)


def finish(part, title, bodies=(), color=None):
    part.SetUserAttribute("DB_PART_NAME", -1, title, NXOpen.Update.Option.Now)
    if color is not None and bodies:
        dm = session.DisplayManager.NewDisplayModification()
        dm.NewColor = color
        dm.Apply(list(bodies))
        dm.Dispose()
    part.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.TrueValue)


def block(part, x, y, z, dx, dy, dz):
    b = part.Features.CreateBlockFeatureBuilder(NXOpen.Features.Feature.Null)
    b.Type = NXOpen.Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths
    b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
    b.SetOriginAndLengths(P(x, y, z), str(dx), str(dy), str(dz))
    f = b.CommitFeature()
    b.Destroy()
    return f.GetBodies()


def cylinder(part, x, y, z, d, h):
    b = part.Features.CreateCylinderBuilder(NXOpen.Features.Feature.Null)
    b.Type = NXOpen.Features.CylinderBuilder.Types.AxisDiameterAndHeight
    b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
    b.Diameter.SetFormula(str(d))
    b.Height.SetFormula(str(h))
    direction = part.Directions.CreateDirection(P(x, y, z), NXOpen.Vector3d(0.0, 0.0, 1.0), UPD)
    b.Axis = part.Axes.CreateAxis(part.Points.CreatePoint(P(x, y, z)), direction, UPD)
    f = b.CommitFeature()
    b.Destroy()
    return f.GetBodies()


def make_part(name, title, blocks=(), cylinders=(), color=None):
    part = new_part(name)
    bodies = []
    for bl in blocks:
        bodies += block(part, *bl)
    for cy in cylinders:
        bodies += cylinder(part, *cy)
    finish(part, title, bodies, color)


def make_assembly(name, title, children):
    """children: (деталь, x, y, z[, угол_вокруг_Z])"""
    asm = new_part(name)
    for ch in children:
        part_name, x, y, z = ch[:4]
        angle = ch[4] if len(ch) > 4 else 0
        asm.ComponentAssembly.AddComponent(path_of(part_name), "Entire Part", part_name, P(x, y, z), rot_z(angle), -1)
    finish(asm, title)
    return path_of(name)


session.Parts.LoadOptions.ComponentsToLoad = NXOpen.LoadOptions.LoadComponents.All
session.Parts.LoadOptions.UsePartialLoading = False

GREY, GREEN, BLUE, YELLOW = 130, 36, 211, 6

# ---------------- оснастка 0912-4410 ----------------
make_part("0912-4410.001", "Плита основания", [(-150, -150, 0, 1500, 1100, 30)], color=GREY)
make_part("0912-4410.002", "Опора", [(0, 0, 0, 80, 80, 70)], color=GREY)
make_part("0912-4410.003", "Упор угловой", [(0, 0, 0, 100, 25, 160), (0, 25, 0, 25, 75, 160)], color=GREY)
make_part("0912-4410.004", "Палец фиксирующий", cylinders=[(0, 0, 0, 24, 150)], color=GREEN)
# прижим: корпус 40x40x130 от начала координат, рычаг вдоль +Y над деталью
make_part("0912-4410.101", "Корпус прижима", [(0, 0, 0, 40, 40, 130)], color=BLUE)
make_part("0912-4410.102", "Рычаг прижима", [(12.5, 0, 130, 15, 115, 10)], cylinders=[(20, 105, 100, 16, 30)],
          color=YELLOW)
make_assembly("0912-4410.100", "Прижим вертикальный", [("0912-4410.101", 0, 0, 0), ("0912-4410.102", 0, 0, 0)])
# покупной пневмоприжим: одно тело, рычаг вдоль +Y
make_part("PNEUMO_DCL40", "Пневмоприжим DCL-40",
          [(0, 0, 0, 50, 50, 130), (17.5, 0, 130, 15, 125, 12)], cylinders=[(25, 25, -60, 40, 60)], color=31)

supports = [(x, y, 30) for x in (100, 560, 1020) for y in (-10, 730)]
clamps_front = [(x, -70, 30, 0) for x in (230, 690, 1100)]           # рычаг к +Y на передний лонжерон
clamps_back = [(x + 40, 870, 30, 180) for x in (230, 690, 1100)]     # повёрнуты на 180°, рычаг к −Y
fixture = [("0912-4410.001", 0, 0, 0)]
fixture += [("0912-4410.002",) + s for s in supports]
fixture += [
    ("0912-4410.003", -110, -110, 30, 0),
    ("0912-4410.003", 1310, -110, 30, 90),
    ("0912-4410.003", 1310, 910, 30, 180),
    ("0912-4410.003", -110, 910, 30, 270),
    ("0912-4410.004", -20, 400, 30),
    ("0912-4410.004", 1220, 400, 30),
]
fixture += [("0912-4410.100",) + c for c in clamps_front + clamps_back]
fixture += [("PNEUMO_DCL40", 485, 200, 30, -90), ("PNEUMO_DCL40", 485, 650, 30, -90)]
make_assembly("0912-4410.000.01", "Оснастка сборочно-сварочная", fixture)

# ---------------- рама 2210-0035 (низ рамы на Z=100) ----------------
make_part("2210-0035.001", "Лонжерон", [(0, 0, 0, 1200, 60, 60)], color=YELLOW)
make_part("2210-0035.002", "Поперечина торцевая", [(0, 0, 0, 60, 680, 60)], color=YELLOW)
make_part("2210-0035.003", "Поперечина средняя", [(0, 0, 0, 60, 680, 60)], color=YELLOW)
make_part("2210-0035.004", "Косынка", [(0, 0, 0, 90, 90, 8)], color=YELLOW)
make_part("2210-0035.005", "Проушина", [(0, 0, 0, 80, 12, 90)], color=YELLOW)
make_part("2210-0035.011", "Полка кронштейна", [(0, 0, 0, 160, 120, 10)], color=YELLOW)
make_part("2210-0035.012", "Ребро кронштейна", [(75, 0, 10, 10, 120, 70)], color=YELLOW)
make_assembly("2210-0035.010", "Кронштейн", [("2210-0035.011", 0, 0, 0), ("2210-0035.012", 0, 0, 0)])

Z = 100
top = make_assembly("FRAME_OPERATION", "Сварка рамы 2210-0035.000", [
    ("0912-4410.000.01", 0, 0, 0),
    ("2210-0035.001", 0, 0, Z),
    ("2210-0035.001", 0, 740, Z),
    ("2210-0035.002", 0, 60, Z),
    ("2210-0035.002", 1140, 60, Z),
    ("2210-0035.003", 570, 60, Z),
    ("2210-0035.004", 60, 60, Z + 60),
    ("2210-0035.004", 1050, 60, Z + 60),
    ("2210-0035.004", 1050, 650, Z + 60),
    ("2210-0035.004", 60, 650, Z + 60),
    ("2210-0035.005", 300, -12, Z - 15),
    ("2210-0035.005", 820, -12, Z - 15),
    ("2210-0035.010", 520, 340, Z + 60),
])
print("OK", top)
