"""Построение 3D-сцены этапа (PyVista): оснастка, детали, прижимы, стрелки."""
from __future__ import annotations

import inspect
import os
from collections import OrderedDict

import numpy as np
import pyvista as pv
from vtkmodules.vtkCommonCore import VTK_FONT_FILE
from vtkmodules.vtkRenderingCore import vtkCoordinate, vtkMapper

# рёбра поверх граней без мерцания
vtkMapper.SetResolveCoincidentTopologyToPolygonOffset()

from .model import FIXTURE, MOVABLE, PART, AssemblyModel, Classifier, Node
from .scenario import DIRECTIONS, Scenario

COLOR_FIXTURE = "#9ea4ab"
COLOR_PART_DONE = "#7D8FA3"
COLOR_PART_NEW = "#D7262D"
COLOR_MOVABLE = "#2E9D5B"
COLOR_EDGES = "#202020"
COLOR_REMOVED = "#44474D"   # стрелка и подпись убираемого
REMOVED_OPACITY = 0.28
COLOR_BG = "white"

DEFAULT_VIEW = np.array([0.75, -1.0, 0.85])
COLOR_SELECTION = "#1E88E5"
LABEL_FONT = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")
MERGE_CACHE_SIZE = 48


def add_labels(plotter: pv.Plotter, points, texts, **kw):
    """Подписи с кириллицей (шрифт Arial). Старый PyVista (сборка для Windows 7) не знает font_file —
    тогда шрифт задаётся свойству текста VTK напрямую."""
    has_font = os.path.exists(LABEL_FONT)
    if has_font and "font_file" in inspect.signature(plotter.add_point_labels).parameters:
        return plotter.add_point_labels(points, texts, font_file=LABEL_FONT, **kw)
    actor = plotter.add_point_labels(points, texts, **kw)
    if has_font:
        try:
            prop = actor.GetMapper().GetInputAlgorithm().GetTextProperty()
            prop.SetFontFamily(VTK_FONT_FILE)
            prop.SetFontFile(LABEL_FONT)
        except Exception:
            pass
    return actor


class SceneBuilder:
    def __init__(self, model: AssemblyModel):
        self.model = model
        self._meshes: dict[str, pv.PolyData] = {}
        self._edges: dict[str, pv.PolyData] = {}
        self._merged: OrderedDict = OrderedDict()   # набор файлов -> (сетка, рёбра); тысячи тел шоп-файла
        self.arrows: list[dict] = []        # стрелки последней построенной сцены: key, kind, anchor, free (мир)
        self.last_arrows: list[dict] = []   # они же на последнем снимке render_step (доли снимка)

    # --- геометрия ---
    def _leaf_mesh(self, path: str) -> pv.PolyData:
        m = self._meshes.get(path)
        if m is None:
            m = pv.read(path).clean()
            self._meshes[path] = m
            self._edges[path] = m.extract_feature_edges(
                feature_angle=35, boundary_edges=True, non_manifold_edges=False, manifold_edges=False)
        return m

    def node_mesh(self, nodes: list[Node], cls: Classifier | None = None, role: str | None = None
                  ) -> tuple[pv.PolyData | None, pv.PolyData | None]:
        """Объединённая сетка узлов; с role — только листья с этой ролью."""
        files = [n.mesh for top in nodes for n in top.walk()
                 if n.mesh and (role is None or cls.role(n) == role)]
        if not files:
            return None, None
        cache_key = tuple(files)
        if cache_key in self._merged:
            self._merged.move_to_end(cache_key)
            return self._merged[cache_key]
        meshes = [self._leaf_mesh(f) for f in files]
        edges = [self._edges[f] for f in files if self._edges[f].n_points]
        mesh = meshes[0] if len(meshes) == 1 else pv.merge(meshes)
        edge = None
        if edges:
            edge = edges[0] if len(edges) == 1 else pv.merge(edges)
        self._merged[cache_key] = (mesh, edge)
        if len(self._merged) > MERGE_CACHE_SIZE:
            self._merged.popitem(last=False)
        return mesh, edge

    def meshes_by_color(self, nodes: list[Node], cls: Classifier, role: str):
        """Сетки узлов с ролью role, сгруппированные по цвету NX: [(rgb | None, сетка, рёбра)]."""
        groups: dict = {}
        for top in nodes:
            for n in top.walk():
                if n.mesh and cls.role(n) == role:
                    groups.setdefault(self.model.rgb(n), []).append(n.mesh)
        out = []
        for rgb, files in groups.items():
            key = ("color",) + tuple(files)
            if key not in self._merged:
                meshes = [self._leaf_mesh(f) for f in files]
                edges = [self._edges[f] for f in files if self._edges[f].n_points]
                self._merged[key] = (meshes[0] if len(meshes) == 1 else pv.merge(meshes),
                                     (edges[0] if len(edges) == 1 else pv.merge(edges)) if edges else None)
                if len(self._merged) > MERGE_CACHE_SIZE:
                    self._merged.popitem(last=False)
            mesh, edge = self._merged[key]
            out.append((rgb, mesh, edge))
        return out

    def highlight(self, plotter: pv.Plotter, nodes: list[Node]):
        """Подсветка выбранных узлов (поверх сцены, без перестроения)."""
        for name in ("__sel_mesh__", "__sel_box__"):
            plotter.remove_actor(name, render=False)
        mesh, _ = self.node_mesh(nodes) if nodes else (None, None)
        if mesh is not None:
            plotter.add_mesh(mesh, color=COLOR_SELECTION, name="__sel_mesh__", opacity=0.85,
                             smooth_shading=False, reset_camera=False)
            plotter.add_mesh(mesh.outline(), color=COLOR_SELECTION, line_width=2, name="__sel_box__",
                             reset_camera=False)
        plotter.render()
        return mesh

    def model_bounds(self) -> np.ndarray:
        mesh, _ = self.node_mesh([self.model.root])
        return np.array(mesh.bounds) if mesh is not None else np.array([0, 1, 0, 1, 0, 1], float)

    # --- сцена ---
    def build(self, plotter: pv.Plotter, scenario: Scenario, cls: Classifier, step_idx: int | None,
              labels: bool = True, set_camera: bool = True, scale: float = 1.0):
        """scale — множитель толщины линий и шрифта (для скриншотов высокого разрешения)."""
        line_width = 1.2 * scale
        self.arrows = []
        plotter.clear()
        plotter.set_background(COLOR_BG)

        bounds = self.model_bounds()
        diag = float(np.linalg.norm(bounds[1::2] - bounds[0::2])) or 1.0

        nx_colors = getattr(scenario, "color_mode", "roles") == "nx"

        def add(nodes, color, role, keep=False):
            """keep — всегда цвет инструкции (закладываемая деталь), даже в режиме «цвета NX»."""
            if nx_colors and not keep:
                for rgb, mesh, edges in self.meshes_by_color(nodes, cls, role):
                    plotter.add_mesh(mesh, color=rgb or color, smooth_shading=False, specular=0.2, ambient=0.3,
                                     diffuse=0.75)
                    if edges is not None:
                        plotter.add_mesh(edges, color=COLOR_EDGES, line_width=line_width)
                return None
            mesh, edges = self.node_mesh(nodes, cls, role)
            if mesh is None:
                return None
            plotter.add_mesh(mesh, color=color, smooth_shading=False, specular=0.2, ambient=0.3, diffuse=0.75)
            if edges is not None:
                # среди цветов NX закладываемую деталь выделяет ещё и толстый контур
                plotter.add_mesh(edges, color=COLOR_EDGES, line_width=line_width * (2.5 if keep and nx_colors else 1))
            return mesh

        add([self.model.root], COLOR_FIXTURE, FIXTURE)

        if step_idx is None or not scenario.steps:
            # без этапа — показываем всю сборку
            add([self.model.root], COLOR_PART_DONE, PART)
            add([self.model.root], COLOR_MOVABLE, MOVABLE)
            if set_camera:
                self.apply_camera(plotter, None, bounds)
            return

        step = scenario.steps[step_idx]
        done_keys = {k for s in scenario.steps[:step_idx] for k in s.add}
        by_key = self.model.by_key
        done = [by_key[k] for k in done_keys if k in by_key and k not in step.hide]
        new = [by_key[k] for k in step.add if k in by_key]
        shown = [by_key[k] for k in step.show if k in by_key]

        add(done, COLOR_PART_DONE, PART)
        add(shown, COLOR_MOVABLE, MOVABLE)

        direction = np.array(DIRECTIONS.get(step.direction, DIRECTIONS["-Z"])[1], float)
        arrow_len = 0.16 * diag
        gap = 0.02 * diag
        label_pts, label_txt = [], []

        def arrow(key, kind, anchor, free, base_len, color, inward=True):
            """Стрелка детали/прижима с ручной правкой этапа; inward — остриё у детали (закладка)."""
            anchor, free = self._adjusted(step, key, anchor, free)
            start, end = (free, anchor) if inward else (anchor, free)
            plotter.add_mesh(self._arrow_mesh(start, end, base_len), color=color)
            self.arrows.append({"key": key, "kind": kind, "anchor": anchor, "free": free})
            return free

        for n in new:
            mesh = add([n], COLOR_PART_NEW, PART, keep=True)
            if mesh is None:
                continue
            tip = self._entry_point(mesh, direction) - direction * gap
            start = tip - direction * arrow_len
            label_pts.append(arrow(n.key, PART, tip, start, arrow_len, COLOR_PART_NEW))
            label_txt.append(n.short_label)

        prev_shown = set(scenario.steps[step_idx - 1].show) if step_idx > 0 else set()
        down = np.array([0.0, 0.0, -1.0])
        for n in shown:
            if n.key in prev_shown:
                continue  # стрелка только на прижимах, появившихся на этом этапе
            mesh, _ = self.node_mesh([n], cls, MOVABLE)
            if mesh is None:
                continue
            tip = self._entry_point(mesh, down) - down * gap
            start = tip - down * arrow_len * 0.7
            label_pts.append(arrow(n.key, MOVABLE, tip, start, arrow_len * 0.7, COLOR_MOVABLE))
            label_txt.append(n.short_label)

        # убираемое на этапе: полупрозрачно на старом месте + стрелка наружу
        rem_pts, rem_txt = [], []
        rem_parts, rem_clamps = scenario.removed_on(step_idx)
        up = np.array([0.0, 0.0, 1.0])
        for keys, role, color in ((rem_parts, PART, COLOR_PART_DONE), (rem_clamps, MOVABLE, COLOR_MOVABLE)):
            for k in keys:
                n = by_key.get(k)
                mesh, edges = self.node_mesh([n], cls, role) if n else (None, None)
                if mesh is None:
                    continue
                plotter.add_mesh(mesh, color=color, opacity=REMOVED_OPACITY, smooth_shading=False)
                if edges is not None:
                    plotter.add_mesh(edges, color=COLOR_REMOVED, opacity=0.5, line_width=line_width)
                if role == PART:  # деталь уходит обратно туда, откуда её закладывали
                    placed = scenario.step_of_part(k)
                    d_in = DIRECTIONS.get(scenario.steps[placed].direction if placed is not None else "-Z",
                                          DIRECTIONS["-Z"])[1]
                    out = -np.array(d_in, float)
                else:
                    out = up
                length = arrow_len * (1.0 if role == PART else 0.7)
                start = self._entry_point(mesh, -out) + out * gap
                rem_pts.append(arrow(k, "removed", start, start + out * length, length, COLOR_REMOVED, inward=False))
                rem_txt.append(n.short_label)

        if labels and label_pts:
            add_labels(plotter, np.array(label_pts), label_txt, font_size=int(13 * scale), point_size=1,
                       shape_opacity=0.9, shape_color="white", text_color="#1F2023",
                       margin=4, always_visible=True, show_points=False)
        if labels and rem_pts:
            add_labels(plotter, np.array(rem_pts), rem_txt, font_size=int(13 * scale), point_size=1,
                       shape_opacity=0.9, shape_color=COLOR_REMOVED, text_color="white",
                       margin=4, always_visible=True, show_points=False)

        if set_camera:
            self.apply_camera(plotter, step.camera, bounds)

    @staticmethod
    def _adjusted(step, key, anchor, free):
        """Концы стрелки с ручной правкой: anchor сдвигает всю стрелку, free — ещё и свободный конец."""
        adj = (getattr(step, "arrows", None) or {}).get(key) or {}
        a = np.array(adj.get("anchor", (0, 0, 0)), float)
        f = np.array(adj.get("free", (0, 0, 0)), float)
        return anchor + a, free + a + f

    @staticmethod
    def _arrow_mesh(start, end, base_len: float) -> pv.PolyData:
        """Стрелка от start к end; толщина и наконечник — как у стрелки длиной base_len, при любой длине."""
        vec = np.asarray(end, float) - np.asarray(start, float)
        length = float(np.linalg.norm(vec))
        if length < 1e-6:
            vec, length = np.array([0.0, 0.0, -1.0]), 1e-6
        k = base_len / length
        return pv.Arrow(start=start, direction=vec / length, tip_length=min(0.3 * k, 0.6), tip_radius=0.09 * k,
                        shaft_radius=0.035 * k, scale=length)

    @staticmethod
    def _to_frac(plotter: pv.Plotter, point, shape) -> tuple[float, float]:
        """Точка мира на снимке plotter: доли ширины и высоты (y сверху вниз)."""
        coord = vtkCoordinate()
        coord.SetCoordinateSystemToWorld()
        coord.SetValue(*[float(v) for v in point])
        x, y = coord.GetComputedDoubleDisplayValue(plotter.renderer)[:2]
        return x / shape[1], 1.0 - y / shape[0]

    def _project_arrows(self, plotter: pv.Plotter, shape) -> list[dict]:
        """Концы стрелок на снимке и как сдвиг на снимке переводится в сдвиг в мире (плоскость экрана)."""
        cam = plotter.camera
        view = np.array(cam.focal_point, float) - np.array(cam.position, float)
        view /= np.linalg.norm(view) or 1.0
        right = np.cross(view, np.array(cam.up, float))
        right /= np.linalg.norm(right) or 1.0
        up = np.cross(right, view)
        b = self.model_bounds()
        s = 0.01 * (float(np.linalg.norm(b[1::2] - b[0::2])) or 1.0)
        out = []
        for a in self.arrows:
            info = {"key": a["key"], "kind": a["kind"], "right": right, "up": up}
            for end in ("anchor", "free"):
                p = np.array(a[end], float)
                p0 = np.array(self._to_frac(plotter, p, shape))
                pr = np.array(self._to_frac(plotter, p + right * s, shape))
                pu = np.array(self._to_frac(plotter, p + up * s, shape))
                info[end] = (float(p0[0]), float(p0[1]))
                info[end + "_jac"] = np.column_stack([(pr - p0) / s, (pu - p0) / s])
            out.append(info)
        return out

    @staticmethod
    def frac_to_world(info: dict, end: str, dx: float, dy: float) -> list[float]:
        """Сдвиг конца стрелки на снимке (доли) -> сдвиг в мире в плоскости экрана (мм)."""
        try:
            a, b = np.linalg.solve(info[end + "_jac"], np.array([dx, dy], float))
        except np.linalg.LinAlgError:
            return [0.0, 0.0, 0.0]
        return [float(v) for v in a * info["right"] + b * info["up"]]

    @staticmethod
    def _entry_point(mesh: pv.PolyData, direction: np.ndarray) -> np.ndarray:
        """Точка на габарите детали со стороны, откуда она подаётся."""
        b = np.array(mesh.bounds)
        center = (b[0::2] + b[1::2]) / 2
        half = (b[1::2] - b[0::2]) / 2
        return center - direction * float(np.abs(direction) @ half)

    @staticmethod
    def apply_camera(plotter: pv.Plotter, camera: dict | None, bounds: np.ndarray):
        if camera:
            plotter.camera.position = camera["position"]
            plotter.camera.focal_point = camera["focal_point"]
            plotter.camera.up = camera["view_up"]
            plotter.camera.parallel_projection = "parallel_scale" in camera
            plotter.camera.view_angle = float(camera.get("view_angle", 30.0))
            if "parallel_scale" in camera:
                plotter.camera.parallel_scale = camera["parallel_scale"]
            plotter.reset_camera_clipping_range()
            return
        plotter.camera.parallel_projection = False
        plotter.camera.view_angle = 30.0
        center = (bounds[0::2] + bounds[1::2]) / 2
        diag = float(np.linalg.norm(bounds[1::2] - bounds[0::2])) or 1.0
        v = DEFAULT_VIEW / np.linalg.norm(DEFAULT_VIEW)
        plotter.camera.position = tuple(center + v * diag * 1.8)
        plotter.camera.focal_point = tuple(center)
        plotter.camera.up = (0, 0, 1)
        # по габаритам модели с запасом под стрелки, а не по всей сцене: сдвиг стрелки не двигает вид
        pad = 0.08 * diag
        plotter.reset_camera(bounds=[bounds[0] - pad, bounds[1] + pad, bounds[2] - pad, bounds[3] + pad,
                                     bounds[4] - pad, bounds[5] + pad])
        plotter.camera.zoom(1.2)

    @staticmethod
    def capture_camera(plotter: pv.Plotter) -> dict:
        c = plotter.camera
        d = {
            "position": [round(float(x), 3) for x in c.position],
            "focal_point": [round(float(x), 3) for x in c.focal_point],
            "view_up": [round(float(x), 5) for x in c.up],
        }
        if c.parallel_projection:
            d["parallel_scale"] = round(float(c.parallel_scale), 3)
        elif abs(float(c.view_angle) - 30.0) > 0.01:   # приближение «зумом» меняет угол обзора, а не положение
            d["view_angle"] = round(float(c.view_angle), 3)
        return d

    def screenshot(self, scenario: Scenario, cls: Classifier, step_idx: int | None,
                   size=(1800, 1150)) -> np.ndarray:
        return self.render_step(scenario, cls, step_idx, size)[0]

    def render_step(self, scenario: Scenario, cls: Classifier, step_idx: int | None, size,
                    camera: dict | None = None, marks=(), scale: float | None = None, aa: bool = True):
        """Снимок этапа (camera — свой ракурс, например уточнения) и положение мест marks на снимке.

        marks — ракурсы уточнений; для каждого — (x, y, r) в долях снимка (y сверху вниз, r — доля высоты)
        или None, если место не попало в кадр."""
        size = [max(int(size[0]), 16), max(int(size[1]), 16)]
        p = pv.Plotter(off_screen=True, window_size=size)
        try:
            self.build(p, scenario, cls, step_idx, scale=scale or max(1.0, size[0] / 1000),
                       set_camera=camera is None)
            if camera is not None:
                self.apply_camera(p, camera, self.model_bounds())
            if aa:
                p.enable_anti_aliasing("ssaa")
            img = p.screenshot(return_img=True)
            self.last_arrows = self._project_arrows(p, img.shape)
            return img, [self._project(p, cam, img.shape) for cam in marks]
        finally:
            p.close()

    @staticmethod
    def view_radius(camera: dict) -> float:
        """Половина высоты кадра ракурса camera в его центре (мм)."""
        if "parallel_scale" in camera:
            return float(camera["parallel_scale"])
        dist = float(np.linalg.norm(np.array(camera["position"]) - np.array(camera["focal_point"])))
        return dist * float(np.tan(np.radians(float(camera.get("view_angle", 30.0))) / 2))

    @staticmethod
    def _project(plotter: pv.Plotter, camera: dict, shape):
        """Центр и размер кадра ракурса camera на снимке plotter."""
        h, w = shape[0], shape[1]
        coord = vtkCoordinate()
        coord.SetCoordinateSystemToWorld()
        ren = plotter.renderer
        center = np.array(camera["focal_point"], float)
        coord.SetValue(*center)
        x, y = coord.GetComputedDoubleDisplayValue(ren)[:2]
        up = np.array(plotter.camera.up, float)
        up /= np.linalg.norm(up) or 1.0
        coord.SetValue(*(center + up * SceneBuilder.view_radius(camera)))
        x2, y2 = coord.GetComputedDoubleDisplayValue(ren)[:2]
        fx, fy, r = x / w, 1.0 - y / h, float(np.hypot(x2 - x, y2 - y)) / h
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            return None
        return fx, fy, r

    def zoom_camera(self, plotter: pv.Plotter, nodes: list[Node]) -> dict | None:
        """Ракурс уточнения на узлы nodes: направление взгляда — как сейчас в plotter, узлы — крупно в центре."""
        mesh, _ = self.node_mesh(nodes)
        if mesh is None:
            return None
        back = self.capture_camera(plotter)
        b = np.array(mesh.bounds, float)
        pad = max(float(np.max(b[1::2] - b[0::2])) * 0.35, 1.0)
        b[0::2] -= pad
        b[1::2] += pad
        plotter.reset_camera(bounds=b)
        cam = self.capture_camera(plotter)
        self.apply_camera(plotter, back, self.model_bounds())
        return cam
