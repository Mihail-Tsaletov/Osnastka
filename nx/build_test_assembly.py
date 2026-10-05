"""NX-журнал: строит тестовую сборку оснастки из простых блоков.

Запуск: run_journal.exe build_test_assembly.py -args <папка_вывода>

Структура:
  TEST_OPERATION.prt                      — верхняя сборка операции
    0898-8264.000.01                      — оснастка (подсборка)
      0898-8264.001  плита                 x1
      0898-8264.002  стойка                x2
      0898-8264.003  упор                  x1
      0898-8264.004  выдвижной палец       x1
      0898-8264.100  прижим (подсборка)    x3
        0898-8264.101  корпус прижима
        0898-8264.102  рычаг прижима
      DESTACO_247U   покупной прижим (без префикса)
    1234-5678.001  балка
    1234-5678.002  косынка                 x2 (одинаковые экземпляры)
    1234-5678.010  кронштейн (подсборка из двух деталей)
      1234-5678.011  полка
      1234-5678.012  ребро
"""
import os
import sys

import NXOpen
import NXOpen.Features
import NXOpen.GeometricUtilities

session = NXOpen.Session.GetSession()
out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "test_model")
out_dir = os.path.abspath(out_dir)
os.makedirs(out_dir, exist_ok=True)

IDENTITY = NXOpen.Matrix3x3()
IDENTITY.Xx, IDENTITY.Xy, IDENTITY.Xz = 1.0, 0.0, 0.0
IDENTITY.Yx, IDENTITY.Yy, IDENTITY.Yz = 0.0, 1.0, 0.0
IDENTITY.Zx, IDENTITY.Zy, IDENTITY.Zz = 0.0, 0.0, 1.0


def path_of(name):
    return os.path.join(out_dir, name + ".prt")


def save_and_close(part):
    part.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.TrueValue)


def make_part(name, blocks, color=None):
    """blocks: список (x, y, z, dx, dy, dz) — каждое отдельное тело."""
    p = path_of(name)
    if os.path.exists(p):
        os.remove(p)
    part = session.Parts.NewDisplay(p, NXOpen.Part.Units.Millimeters)
    bodies = []
    for (x, y, z, dx, dy, dz) in blocks:
        b = part.Features.CreateBlockFeatureBuilder(NXOpen.Features.Feature.Null)
        b.Type = NXOpen.Features.BlockFeatureBuilder.Types.OriginAndEdgeLengths
        b.BooleanOption.Type = NXOpen.GeometricUtilities.BooleanOperation.BooleanType.Create
        b.SetOriginAndLengths(NXOpen.Point3d(float(x), float(y), float(z)), str(dx), str(dy), str(dz))
        feat = b.CommitFeature()
        b.Destroy()
        bodies.extend(feat.GetBodies())
    if color is not None and bodies:
        dm = session.DisplayManager.NewDisplayModification()
        dm.NewColor = color
        dm.Apply(bodies)
        dm.Dispose()
    save_and_close(part)
    return p


def make_assembly(name, children):
    """children: список (имя_детали, имя_компонента, x, y, z)."""
    p = path_of(name)
    if os.path.exists(p):
        os.remove(p)
    asm = session.Parts.NewDisplay(p, NXOpen.Part.Units.Millimeters)
    for part_name, comp_name, x, y, z in children:
        asm.ComponentAssembly.AddComponent(
            path_of(part_name), "Entire Part", comp_name, NXOpen.Point3d(float(x), float(y), float(z)), IDENTITY, -1)
    save_and_close(asm)
    return p


session.Parts.LoadOptions.ComponentsToLoad = NXOpen.LoadOptions.LoadComponents.All
session.Parts.LoadOptions.UsePartialLoading = False

# --- детали оснастки ---
make_part("0898-8264.001", [(0, 0, 0, 600, 400, 20)], color=130)          # плита
make_part("0898-8264.002", [(0, 0, 0, 40, 40, 60)], color=130)            # стойка
make_part("0898-8264.003", [(0, 0, 0, 20, 40, 100)], color=130)           # упор
make_part("0898-8264.004", [(0, 0, 0, 16, 16, 50)], color=130)            # палец
make_part("0898-8264.101", [(0, 0, 0, 40, 30, 100)], color=211)           # корпус прижима
make_part("0898-8264.102", [(12.5, -70, 100, 15, 100, 10)], color=186)    # рычаг прижима
make_assembly("0898-8264.100", [
    ("0898-8264.101", "0898-8264.101", 0, 0, 0),
    ("0898-8264.102", "0898-8264.102", 0, 0, 0),
])
# покупной прижим одним телом (корпус + рычаг)
make_part("DESTACO_247U", [(0, 0, 0, 30, 40, 100), (7.5, 0, 100, 15, 90, 10)], color=31)

make_assembly("0898-8264.000.01", [
    ("0898-8264.001", "0898-8264.001", 0, 0, 0),
    ("0898-8264.002", "0898-8264.002", 50, 180, 20),
    ("0898-8264.002", "0898-8264.002", 510, 180, 20),
    ("0898-8264.003", "0898-8264.003", 575, 180, 20),
    ("0898-8264.004", "0898-8264.004", 12, 192, 80),
    ("0898-8264.100", "0898-8264.100", 60, 240, 20),
    ("0898-8264.100", "0898-8264.100", 240, 240, 20),
    ("0898-8264.100", "0898-8264.100", 470, 240, 20),
    ("DESTACO_247U", "DESTACO_247U", 370, 130, 20),
])

# --- свариваемые детали ---
make_part("1234-5678.001", [(0, 0, 0, 540, 40, 40)], color=6)             # балка
make_part("1234-5678.002", [(0, 0, 0, 60, 10, 60)], color=6)              # косынка
make_part("1234-5678.011", [(0, 0, 0, 80, 80, 10)], color=6)              # полка кронштейна
make_part("1234-5678.012", [(35, 0, 10, 10, 80, 50)], color=6)            # ребро кронштейна
make_assembly("1234-5678.010", [
    ("1234-5678.011", "1234-5678.011", 0, 0, 0),
    ("1234-5678.012", "1234-5678.012", 0, 0, 0),
])

top = make_assembly("TEST_OPERATION", [
    ("0898-8264.000.01", "0898-8264.000.01", 0, 0, 0),
    ("1234-5678.001", "1234-5678.001", 30, 180, 80),
    ("1234-5678.002", "1234-5678.002", 120, 220, 80),
    ("1234-5678.002", "1234-5678.002", 400, 220, 80),
    ("1234-5678.010", "1234-5678.010", 260, 160, 120),
])
print("OK", top)
