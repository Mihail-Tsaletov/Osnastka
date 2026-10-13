"""Окно «Выбор по модели»: щелчок по детали в 3D выбирает её в дереве и в таблице этапов основного окна.

Вся модель рисуется несколькими объединёнными сетками (по ролям); у каждой ячейки сетки — номер узла
(массив "nid"), поэтому щелчок однозначно определяет деталь, даже среди тысяч тел «шоп»-файла.
"""
from __future__ import annotations

import numpy as np
import pyvista as pv
from qtpy.QtCore import Qt, QTimer
from qtpy.QtGui import QCursor
from qtpy.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)
from pyvistaqt import QtInteractor
from vtkmodules.vtkRenderingCore import vtkCellPicker

from . import theme
from .model import FIXTURE, HIDDEN, MOVABLE, PART, ROLE_TITLES, Node
from .scene import COLOR_EDGES, COLOR_FIXTURE, COLOR_MOVABLE, COLOR_SELECTION, SceneBuilder, add_labels

ROLE_VIEW_COLORS = {FIXTURE: COLOR_FIXTURE, PART: "#E0574F", MOVABLE: COLOR_MOVABLE, HIDDEN: "#D9DCE0"}
CLICK_TOLERANCE_PX = 5


class PickerWindow(QWidget):
    def __init__(self, main):
        super().__init__(None, Qt.Window)
        self.main = main                 # MainWindow
        self.setWindowTitle("Выбор по модели")
        self.resize(900, 760)
        self._leaves: list[Node] = []    # nid -> лист с геометрией
        self._nid_of: dict[str, int] = {}
        self._actors = {}
        self._full = {}                  # роль -> объединённая сетка всех её листьев (с массивом nid)
        self._edges_full = None
        self._hidden_keys: set[str] = set()   # скрытые в этом окне листья (только вид, на сценарий не влияет)
        self._press = None
        self._rpress = None
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._update_hover)
        self._hover_pos = None
        self._picker = vtkCellPicker()
        self._picker.SetTolerance(0.0005)
        self._build_ui()

    # ------------------------------------------------------------------ интерфейс
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.addWidget(QLabel("Выбирать:"))
        self.level = QComboBox()
        self.level.addItem("как в таблице (деталь, прижим, группа)", "unit")
        self.level.addItem("отдельный компонент / тело", "leaf")
        self.level.setToolTip("«Как в таблице» — щелчок выбирает строку таблицы этапов целиком; "
                              "«отдельный» — конкретную деталь или тело (например, чтобы собрать группу)")
        top.addWidget(self.level)
        top.addSpacing(10)
        self.fixture_glass = QCheckBox("Оснастка полупрозрачная")
        self.fixture_glass.setToolTip("Чтобы добраться до деталей внутри оснастки")
        self.fixture_glass.toggled.connect(self._apply_opacity)
        top.addWidget(self.fixture_glass)
        self.hide_fixture = QCheckBox("Скрыть оснастку")
        self.hide_fixture.toggled.connect(self._apply_opacity)
        top.addWidget(self.hide_fixture)
        top.addStretch()
        b = QPushButton("Вид как в основном окне")
        b.clicked.connect(self.copy_main_camera)
        top.addWidget(b)
        lay.addLayout(top)

        vis = QHBoxLayout()
        vis.addWidget(QLabel("Только в этом окне:"))
        for text, slot, tip in (
                ("Скрыть выбранное", lambda: self.hide_keys(self.main._selected_keys()),
                 "Погасить выбранные детали, чтобы добраться до тех, что за ними (правый щелчок по детали — тоже)"),
                ("Показать только выбранное", lambda: self.isolate_keys(self.main._selected_keys()),
                 "Оставить в окне только выбранное"),
                ("Показать всё", self.show_all_hidden, "Вернуть все скрытые детали")):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            vis.addWidget(btn)
        self.hidden_label = QLabel("")
        self.hidden_label.setObjectName("hint")
        vis.addWidget(self.hidden_label)
        vis.addStretch()
        lay.addLayout(vis)

        self.plotter = QtInteractor(self)
        self.plotter.set_background(theme.VIEW_BG_BOTTOM, top=theme.VIEW_BG_TOP)
        lay.addWidget(self.plotter.interactor, 1)
        self.plotter.interactor.setMouseTracking(True)   # движение без нажатой кнопки — для подсказки под курсором
        iren = self.plotter.iren
        iren.add_observer("LeftButtonPressEvent", self._on_press)
        iren.add_observer("LeftButtonReleaseEvent", self._on_release)
        iren.add_observer("MouseMoveEvent", self._on_move)
        iren.add_observer("RightButtonPressEvent", self._on_rpress)
        iren.add_observer("RightButtonReleaseEvent", self._on_rrelease)

        self.info = QLabel("Щёлкните по детали — она выберется в списке. Ctrl+щелчок — добавить к выбору, "
                           "щелчок по пустому месту — снять выбор.")
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        acts = QHBoxLayout()
        self.selected_label = QLabel("Ничего не выбрано")
        self.selected_label.setObjectName("chipMuted")
        self.selected_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        acts.addWidget(self.selected_label, 1)
        self.btn_step = QPushButton("На текущий этап")
        self.btn_step.setObjectName("primary")
        self.btn_step.setToolTip("Детали — заложить на текущем этапе; прижимы — показать на текущем этапе")
        self.btn_step.clicked.connect(lambda: self.main.apply_to_current_step(self.main._selected_keys(), True))
        acts.addWidget(self.btn_step)
        self.btn_unstep = QPushButton("Снять с этапа")
        self.btn_unstep.clicked.connect(lambda: self.main.apply_to_current_step(self.main._selected_keys(), False))
        acts.addWidget(self.btn_unstep)
        self.btn_role = QPushButton("Роль…")
        self.btn_role.clicked.connect(self._role_menu)
        acts.addWidget(self.btn_role)
        self.btn_group = QPushButton("В группу…")
        self.btn_group.clicked.connect(self._group)
        acts.addWidget(self.btn_group)
        lay.addLayout(acts)
        self.update_selection_label([])

    # ------------------------------------------------------------------ сцена
    def rebuild(self, reset_camera=False):
        """Перестроить сцену по текущей модели и ролям (после смены ролей, групп, замен)."""
        main = self.main
        if not main.model or not main.cls:
            return
        scene: SceneBuilder = main.scene
        cls = main.cls
        self._leaves = [n for n in main.model.root.walk() if n.mesh]
        self._nid_of = {n.key: i for i, n in enumerate(self._leaves)}
        self._hidden_keys &= set(self._nid_of)   # после смены модели — только существующие
        by_role: dict[str, list] = {}
        edges = []
        for nid, n in enumerate(self._leaves):
            role = cls.role(n) or FIXTURE
            m = scene._leaf_mesh(n.mesh).copy(deep=False)
            m.cell_data["nid"] = np.full(m.n_cells, nid, dtype=np.int32)
            by_role.setdefault(role, []).append(m)
            e = scene._edges[n.mesh]
            if e.n_cells:
                e = e.copy(deep=False)
                e.cell_data["nid"] = np.full(e.n_cells, nid, dtype=np.int32)
                edges.append(e)
        self._full = {role: (ms[0] if len(ms) == 1 else pv.merge(ms)) for role, ms in by_role.items()}
        self._edges_full = (edges[0] if len(edges) == 1 else pv.merge(edges)) if edges else None
        p = self.plotter
        cam = p.camera_position if p.renderer.GetActors().GetNumberOfItems() else None
        p.clear()
        p.set_background(theme.VIEW_BG_BOTTOM, top=theme.VIEW_BG_TOP)
        p.add_axes()
        self._draw()
        if reset_camera or cam is None:
            self.copy_main_camera()
        else:
            p.camera_position = cam
        self.show_selection(main._selected_keys())

    def _visible(self, mesh):
        """Сетка без скрытых листьев."""
        if not self._hidden_keys or mesh is None:
            return mesh
        hidden = np.fromiter((self._nid_of[k] for k in self._hidden_keys), dtype=np.int32)
        keep = np.flatnonzero(~np.isin(mesh.cell_data["nid"], hidden))
        if len(keep) == mesh.n_cells:
            return mesh
        return mesh.extract_cells(keep) if len(keep) else None

    def _draw(self):
        """Нарисовать видимое (после перестройки или скрытия) — без пересборки сеток."""
        p = self.plotter
        for role in list(self._actors):
            p.remove_actor("role_" + role, render=False)
        p.remove_actor("__edges__", render=False)
        self._actors = {}
        for role, full in self._full.items():
            mesh = self._visible(full)
            if mesh is not None and mesh.n_cells:
                self._actors[role] = p.add_mesh(mesh, color=ROLE_VIEW_COLORS[role], smooth_shading=False,
                                                specular=0.2, ambient=0.3, diffuse=0.75, pickable=True,
                                                reset_camera=False, name="role_" + role)
        edges = self._visible(self._edges_full)
        if edges is not None and edges.n_cells:
            p.add_mesh(edges, color=COLOR_EDGES, line_width=1, pickable=False, reset_camera=False, name="__edges__")
        n = len(self._hidden_keys)
        self.hidden_label.setText(f"скрыто деталей: {n}" if n else "")
        self._apply_opacity()

    # ------------------------------------------------------------------ скрыть / показать (только вид)
    def _leaf_keys(self, keys):
        out = set()
        for k in keys:
            node = self.main.model.by_key.get(k) if self.main.model else None
            if node is not None:
                out.update(x.key for x in node.walk() if x.key in self._nid_of)
        return out

    def hide_keys(self, keys):
        leaves = self._leaf_keys(keys)
        if not leaves:
            self.info.setText("Сначала выберите, что скрыть (щелчком), или щёлкните по детали правой кнопкой.")
            return
        self._hidden_keys |= leaves
        self.main.select_in_tree([])   # скрытое не остаётся выбранным
        self._draw()

    def isolate_keys(self, keys):
        leaves = self._leaf_keys(keys)
        if not leaves:
            self.info.setText("Сначала выберите, что оставить (щелчком).")
            return
        self._hidden_keys = set(self._nid_of) - leaves
        self._draw()

    def show_all_hidden(self):
        if self._hidden_keys:
            self._hidden_keys = set()
            self._draw()

    def _apply_opacity(self):
        a = self._actors.get(FIXTURE)
        if a is not None:
            a.SetVisibility(not self.hide_fixture.isChecked())
            a.GetProperty().SetOpacity(0.25 if self.fixture_glass.isChecked() else 1.0)
            a.SetPickable(not self.hide_fixture.isChecked())
        h = self._actors.get(HIDDEN)
        if h is not None:
            h.GetProperty().SetOpacity(0.35)
        self.plotter.render()

    def copy_main_camera(self):
        try:
            self.plotter.camera_position = self.main.plotter.camera_position
        except Exception:
            self.plotter.reset_camera()
        self.plotter.render()

    def show_selection(self, keys):
        """Подсветить выбранные узлы (вызывается и при выборе в основном окне)."""
        main = self.main
        nodes = [main.model.by_key[k] for k in keys if main.model and k in main.model.by_key]
        nodes = [n for n in nodes if n.parent is not None]
        for name in ("__sel__", "__sel_label__"):
            self.plotter.remove_actor(name, render=False)
        if nodes and main.scene:
            mesh, _ = main.scene.node_mesh(nodes)
            if mesh is not None:
                self.plotter.add_mesh(mesh, color=COLOR_SELECTION, name="__sel__", opacity=0.9, pickable=False,
                                      smooth_shading=False, reset_camera=False)
                if len(nodes) <= 8:
                    pts = np.array([np.array(main.scene.node_mesh([n])[0].center) for n in nodes])
                    add_labels(self.plotter, pts, [n.short_label for n in nodes], name="__sel_label__",
                               font_size=12, shape_color="white", shape_opacity=0.9,
                               text_color="#1F2023", always_visible=True, show_points=False, pickable=False)
        self.update_selection_label(nodes)
        self.plotter.render()

    def update_selection_label(self, nodes):
        if not nodes:
            self.selected_label.setText("Ничего не выбрано")
        elif len(nodes) == 1:
            n = nodes[0]
            role = self.main.cls.role(n) if self.main.cls else None
            self.selected_label.setText(f"Выбрано: {n.label}" + (f" — {ROLE_TITLES[role]}" if role else ""))
        else:
            self.selected_label.setText(f"Выбрано: {len(nodes)} шт.")
        for b in (self.btn_step, self.btn_unstep, self.btn_role, self.btn_group):
            b.setEnabled(bool(nodes))

    # ------------------------------------------------------------------ щелчки
    def _pick_leaf(self, x, y) -> Node | None:
        self._picker.Pick(x, y, 0, self.plotter.renderer)
        ds = self._picker.GetDataSet()
        cid = self._picker.GetCellId()
        if ds is None or cid < 0:
            return None
        arr = ds.GetCellData().GetArray("nid")
        if arr is None:
            return None
        nid = int(arr.GetValue(cid))
        return self._leaves[nid] if 0 <= nid < len(self._leaves) else None

    def target_of(self, leaf: Node) -> Node:
        """Что выбирать по щелчку: строку таблицы целиком или сам лист."""
        if self.level.currentData() == "leaf":
            return leaf
        cls, split = self.main.cls, self.main.cls.split
        role = cls.role(leaf)
        if role in (PART, MOVABLE):   # подняться до строки таблицы
            n = leaf
            while (n.parent is not None and n.parent.parent is not None and n.parent.key not in split
                   and cls.role(n.parent) == role):
                n = n.parent
            return n
        # оснастка / скрытое: тело многотельной детали — вся деталь, иначе сам компонент
        if leaf.is_body and leaf.parent is not None and leaf.parent.parent is not None \
                and leaf.parent.key not in split and not leaf.parent.is_group:
            return leaf.parent
        return leaf

    def _source(self, obj):
        """Откуда брать координаты события: PyVista передаёт в обработчик стиль камеры, а не интерактор."""
        return obj if hasattr(obj, "GetEventPosition") else self.plotter.iren.interactor

    def _on_press(self, obj, _event):
        self._press = self._source(obj).GetEventPosition()

    def _on_release(self, obj, _event):
        if self._press is None:
            return
        obj = self._source(obj)
        x, y = obj.GetEventPosition()
        px, py = self._press
        self._press = None
        if abs(x - px) > CLICK_TOLERANCE_PX or abs(y - py) > CLICK_TOLERANCE_PX:
            return   # это было вращение, а не щелчок
        leaf = self._pick_leaf(x, y)
        ctrl = bool(obj.GetControlKey())
        current = self.main._selected_keys()
        if leaf is None:
            if not ctrl:
                self.main.select_in_tree([])
            return
        key = self.target_of(leaf).key
        if ctrl:
            keys = [k for k in current if k != key] if key in current else current + [key]
        else:
            keys = [key]
        self.main.select_in_tree(keys, reveal=True)

    def _on_rpress(self, obj, _event):
        self._rpress = self._source(obj).GetEventPosition()

    def _on_rrelease(self, obj, _event):
        if self._rpress is None:
            return
        x, y = self._source(obj).GetEventPosition()
        px, py = self._rpress
        self._rpress = None
        if abs(x - px) > CLICK_TOLERANCE_PX or abs(y - py) > CLICK_TOLERANCE_PX:
            return   # правая кнопка с перетаскиванием — масштаб, не меню
        leaf = self._pick_leaf(x, y)
        target = self.target_of(leaf) if leaf is not None else None
        selected = self.main._selected_keys()
        menu = QMenu(self)
        if target is not None:
            menu.addAction(f"Скрыть «{target.short_label}»").triggered.connect(lambda: self.hide_keys([target.key]))
            menu.addAction(f"Оставить только «{target.short_label}»").triggered.connect(
                lambda: self.isolate_keys([target.key]))
            if target is not leaf:
                menu.addAction(f"Скрыть только «{leaf.short_label}»").triggered.connect(
                    lambda: self.hide_keys([leaf.key]))
        if selected:
            menu.addSeparator()
            menu.addAction(f"Скрыть выбранное ({len(selected)})").triggered.connect(
                lambda: self.hide_keys(selected))
            menu.addAction("Показать только выбранное").triggered.connect(lambda: self.isolate_keys(selected))
        menu.addSeparator()
        a = menu.addAction("Показать всё")
        a.setEnabled(bool(self._hidden_keys))
        a.triggered.connect(self.show_all_hidden)
        theme.popup(menu, QCursor.pos())

    def _on_move(self, obj, _event):
        self._hover_pos = self._source(obj).GetEventPosition()
        self._hover_timer.start(90)

    def _update_hover(self):
        if self._hover_pos is None or not self._leaves:
            return
        leaf = self._pick_leaf(*self._hover_pos)
        if leaf is None:
            self.info.setText("Щёлкните по детали — она выберется в списке. Ctrl+щелчок — добавить к выбору, "
                              "щелчок по пустому месту — снять выбор.")
            return
        target = self.target_of(leaf)
        role = self.main.cls.role(target)
        extra = f"  (в составе: {target.label})" if target is not leaf else ""
        self.info.setText(f"Под курсором: {leaf.label} — {ROLE_TITLES.get(role, '')}{extra}")

    # ------------------------------------------------------------------ действия
    def _role_menu(self):
        keys = [k for k in self.main._selected_keys() if k in self.main.model.by_key]
        if not keys:
            return
        menu = QMenu(self)
        for role, title in ROLE_TITLES.items():
            menu.addAction(title).triggered.connect(lambda _=False, r=role: self.main._set_role(keys, r))
        menu.addSeparator()
        menu.addAction("По умолчанию").triggered.connect(lambda: self.main._set_role(keys, None))
        theme.popup(menu, self.btn_role.mapToGlobal(self.btn_role.rect().bottomLeft()))

    def _group(self):
        nodes = [self.main.model.by_key[k] for k in self.main._selected_keys() if k in self.main.model.by_key]
        nodes = [n for n in nodes if n.parent is not None and not n.is_group]
        if len({id(n.parent) for n in nodes}) != 1:
            self.info.setText("В группу можно объединить только элементы одного уровня (с общим родителем). "
                              "Переключите «Выбирать» на «отдельный компонент / тело».")
            return
        self.main._group(nodes)

    def closeEvent(self, e):
        self.hide()
        e.ignore()   # окно только прячется — сцена и камера сохраняются
