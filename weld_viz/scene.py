"""Построение 3D-сцены этапа (PyVista): оснастка, детали, прижимы, стрелки."""
from __future__ import annotations

import numpy as np
import pyvista as pv
from vtkmodules.vtkRenderingCore import vtkMapper

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


class SceneBuilder:
    def __init__(self, model: AssemblyModel):
        self.model = model
        self._meshes: dict[str, pv.PolyData] = {}
        self._edges: dict[str, pv.PolyData] = {}

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
        meshes = [self._leaf_mesh(f) for f in files]
        edges = [self._edges[f] for f in files if self._edges[f].n_points]
        mesh = meshes[0] if len(meshes) == 1 else pv.merge(meshes)
        edge = None
        if edges:
            edge = edges[0] if len(edges) == 1 else pv.merge(edges)
        return mesh, edge

    def model_bounds(self) -> np.ndarray:
        mesh, _ = self.node_mesh([self.model.root])
        return np.array(mesh.bounds) if mesh is not None else np.array([0, 1, 0, 1, 0, 1], float)

    # --- сцена ---
    def build(self, plotter: pv.Plotter, scenario: Scenario, cls: Classifier, step_idx: int | None,
              labels: bool = True, set_camera: bool = True, scale: float = 1.0):
        """scale — множитель толщины линий и шрифта (для скриншотов высокого разрешения)."""
        line_width = 1.2 * scale
        plotter.clear()
        plotter.set_background(COLOR_BG)

        bounds = self.model_bounds()
        diag = float(np.linalg.norm(bounds[1::2] - bounds[0::2])) or 1.0

        def add(nodes, color, role):
            mesh, edges = self.node_mesh(nodes, cls, role)
            if mesh is None:
                return None
            plotter.add_mesh(mesh, color=color, smooth_shading=False, specular=0.2, ambient=0.3, diffuse=0.75)
            if edges is not None:
                plotter.add_mesh(edges, color=COLOR_EDGES, line_width=line_width)
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

        for n in new:
            mesh = add([n], COLOR_PART_NEW, PART)
            if mesh is None:
                continue
            tip = self._entry_point(mesh, direction) - direction * gap
            start = tip - direction * arrow_len
            plotter.add_mesh(pv.Arrow(start=start, direction=direction, tip_length=0.3, tip_radius=0.09,
                                      shaft_radius=0.035, scale=arrow_len), color=COLOR_PART_NEW)
            label_pts.append(start)
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
            plotter.add_mesh(pv.Arrow(start=start, direction=down, tip_length=0.3, tip_radius=0.09,
                                      shaft_radius=0.035, scale=arrow_len * 0.7), color=COLOR_MOVABLE)
            label_pts.append(start)
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
                plotter.add_mesh(pv.Arrow(start=start, direction=out, tip_length=0.3, tip_radius=0.09,
                                          shaft_radius=0.035, scale=length), color=COLOR_REMOVED)
                rem_pts.append(start + out * length)
                rem_txt.append(n.short_label)

        if labels and label_pts:
            plotter.add_point_labels(np.array(label_pts), label_txt, font_size=int(13 * scale), point_size=1,
                                     shape_opacity=0.9, shape_color="white", text_color="#1F2023",
                                     margin=4, always_visible=True, show_points=False)
        if labels and rem_pts:
            plotter.add_point_labels(np.array(rem_pts), rem_txt, font_size=int(13 * scale), point_size=1,
                                     shape_opacity=0.9, shape_color=COLOR_REMOVED, text_color="white",
                                     margin=4, always_visible=True, show_points=False)

        if set_camera:
            self.apply_camera(plotter, step.camera, bounds)

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
            if "parallel_scale" in camera:
                plotter.camera.parallel_scale = camera["parallel_scale"]
            plotter.reset_camera_clipping_range()
            return
        plotter.camera.parallel_projection = False
        center = (bounds[0::2] + bounds[1::2]) / 2
        diag = float(np.linalg.norm(bounds[1::2] - bounds[0::2])) or 1.0
        v = DEFAULT_VIEW / np.linalg.norm(DEFAULT_VIEW)
        plotter.camera.position = tuple(center + v * diag * 1.8)
        plotter.camera.focal_point = tuple(center)
        plotter.camera.up = (0, 0, 1)
        plotter.reset_camera()
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
        return d

    def screenshot(self, scenario: Scenario, cls: Classifier, step_idx: int | None,
                   size=(1800, 1150)) -> np.ndarray:
        p = pv.Plotter(off_screen=True, window_size=list(size))
        try:
            self.build(p, scenario, cls, step_idx, scale=max(1.0, size[0] / 1000))
            p.enable_anti_aliasing("ssaa")
            return p.screenshot(return_img=True)
        finally:
            p.close()
