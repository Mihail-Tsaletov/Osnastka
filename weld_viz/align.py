"""Совмещение узла из отдельного файла с телами «шоп»-детали по геометрии.

Детали узла сопоставляются с телами по форме (объём и габариты в собственных осях — не зависят от поворота),
из надёжных пар по методу Кабша находится поворот + сдвиг. Лишние тела (сварные швы) не мешают:
сопоставляются только детали узла.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pyvista as pv


@dataclass
class Signature:
    centroid: np.ndarray
    volume: float
    extents: np.ndarray   # габариты вдоль главных осей, по убыванию


@dataclass
class AlignResult:
    matrix: np.ndarray        # 4×4: координаты файла узла -> координаты сборки
    matched: int              # сколько деталей узла легло на тела
    total: int                # сколько деталей в узле
    max_error: float          # наибольшее отклонение центров совпавших деталей, мм
    method: str

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.matched == self.total

    @property
    def summary(self) -> str:
        return (f"совпало {self.matched} из {self.total} деталей, "
                f"отклонение до {self.max_error:.2f} мм ({self.method})")


def signature(mesh: pv.PolyData) -> Signature:
    tri = mesh.triangulate()
    sizes = tri.compute_cell_sizes(length=False, volume=False)
    area = np.abs(sizes.cell_data["Area"])
    centers = tri.cell_centers().points
    w = area / area.sum() if area.sum() > 0 else np.full(len(area), 1 / max(len(area), 1))
    centroid = (centers * w[:, None]).sum(axis=0)
    cov = ((centers - centroid) * w[:, None]).T @ (centers - centroid)
    _, axes = np.linalg.eigh(cov)
    proj = (tri.points - centroid) @ axes
    extents = np.sort(proj.max(axis=0) - proj.min(axis=0))[::-1]
    return Signature(centroid, abs(float(tri.volume)), extents)


def _similar(a: Signature, b: Signature) -> bool:
    vol_ok = abs(a.volume - b.volume) <= max(0.04 * max(a.volume, b.volume), 1.0)
    ext_ok = np.all(np.abs(a.extents - b.extents) <= np.maximum(0.03 * np.maximum(a.extents, b.extents), 1.0))
    return bool(vol_ok and ext_ok)


def kabsch(p: np.ndarray, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """R, t: q ≈ R·p + t (без отражения)."""
    pc, qc = p.mean(axis=0), q.mean(axis=0)
    h = (p - pc).T @ (q - qc)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, qc - r @ pc


def _to4(r, t):
    m = np.eye(4)
    m[:3, :3], m[:3, 3] = r, t
    return m


def register(parts: list[pv.PolyData], bodies: list[pv.PolyData], tolerance: float | None = None,
             iterations: int = 4000) -> AlignResult:
    """parts — детали узла (в координатах его файла), bodies — тела шоп-детали (в координатах сборки)."""
    ps = [signature(m) for m in parts]
    bs = [signature(m) for m in bodies]
    pts = np.array([s.centroid for s in bs]) if bs else np.zeros((0, 3))
    if tolerance is None:
        allp = np.vstack([m.points for m in bodies]) if bodies else np.zeros((1, 3))
        tolerance = max(1.0, 0.003 * float(np.linalg.norm(allp.max(axis=0) - allp.min(axis=0))))
    cands = {i: [j for j, b in enumerate(bs) if _similar(p, b)] for i, p in enumerate(ps)}
    usable = [i for i in sorted(cands, key=lambda i: -ps[i].volume) if cands[i]]

    def score(m):
        r, t = m[:3, :3], m[:3, 3]
        matched, err, vol, pairs = 0, 0.0, 0.0, []
        for i in usable:
            c = r @ ps[i].centroid + t
            d = np.linalg.norm(pts[cands[i]] - c, axis=1)
            k = int(np.argmin(d))
            if d[k] <= tolerance:
                matched += 1
                vol += ps[i].volume
                err = max(err, float(d[k]))
                pairs.append((i, cands[i][k]))
        return matched, vol, err, pairs

    best = (np.eye(4), (0, 0.0, 0.0, []), "нет совпадений")
    trials = [(np.eye(4), "без смещения")]
    rng = np.random.default_rng(0)
    for _ in range(iterations):
        if len(usable) < 3:
            break
        # три самые крупные детали чаще: устойчивее
        idx = rng.choice(len(usable), size=3, replace=False,
                         p=np.array([ps[i].volume for i in usable]) / sum(ps[i].volume for i in usable))
        sel = [usable[k] for k in idx]
        js = [cands[i][rng.integers(len(cands[i]))] for i in sel]
        if len(set(js)) < 3:
            continue
        p3 = np.array([ps[i].centroid for i in sel])
        q3 = np.array([bs[j].centroid for j in js])
        if np.linalg.norm(np.cross(p3[1] - p3[0], p3[2] - p3[0])) < 1e-3:
            continue  # почти на одной прямой
        if any(abs(np.linalg.norm(p3[a] - p3[b]) - np.linalg.norm(q3[a] - q3[b])) > 3 * tolerance
               for a, b in itertools.combinations(range(3), 2)):
            continue  # расстояния не сходятся — пара ложная
        r, t = kabsch(p3, q3)
        trials.append((_to4(r, t), "по геометрии"))
    if len(usable) < 3 and usable:  # мало деталей — хотя бы сдвиг по самой крупной
        i = usable[0]
        for j in cands[i]:
            trials.append((_to4(np.eye(3), bs[j].centroid - ps[i].centroid), "сдвиг по крупной детали"))

    for m, method in trials:
        sc = score(m)
        if (sc[0], sc[1]) > (best[1][0], best[1][1]):
            best = (m, sc, method)
        if sc[0] == len(ps):
            break

    m, (matched, _, err, pairs), method = best
    if len(pairs) >= 3 and method != "без смещения":  # уточнение по всем совпавшим парам
        r, t = kabsch(np.array([ps[i].centroid for i, _ in pairs]), np.array([bs[j].centroid for _, j in pairs]))
        refined = _to4(r, t)
        sc = score(refined)
        if sc[0] >= matched:
            m, (matched, _, err, pairs) = refined, sc
    return AlignResult(m, matched, len(ps), err, method)
