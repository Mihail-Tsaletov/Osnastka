"""Десктоп-приложение: настройка сценария закладки и экспорт PDF."""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import QEvent, QObject, QProcess, Qt, QTimer
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressDialog, QPushButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem,
                               QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from pyvistaqt import QtInteractor

from . import nx_bridge, theme
from .model import FIXTURE, HIDDEN, MOVABLE, PART, ROLE_TITLES, AssemblyModel, Classifier, Node
from .pdf_report import export_pdf
from .scenario import DIRECTIONS, Scenario, Step
from .scene import COLOR_FIXTURE, COLOR_MOVABLE, COLOR_PART_NEW, SceneBuilder

APP_TITLE = "Закладка в оснастку"
ROLE_COLORS = {FIXTURE: COLOR_FIXTURE, MOVABLE: COLOR_MOVABLE, PART: COLOR_PART_NEW, HIDDEN: "#c8c8c8"}


def card() -> tuple[QFrame, QVBoxLayout]:
    """Белая карточка со скруглёнными углами."""
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    return f, lay


def label(text, name=None) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    return lb
KEY_ROLE = Qt.UserRole
KEY_KIND = Qt.UserRole + 1


SWITCH_GUARD_SEC = 0.5  # после перехода на этап щелчки по нему не ставят галочки (защита от двойного щелчка)
ARIAL = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arial.ttf")


class NoWheelUnlessFocused(QObject):
    """Колесо мыши не меняет значение списка, пока он не в фокусе (случайная прокрутка)."""

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Wheel and not obj.hasFocus():
            event.ignore()
            return True
        return False


def scenario_path_for(prt: str) -> str:
    return os.path.splitext(prt)[0] + ".weldviz.yaml"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1500, 950)
        self.prt_path: str | None = None
        self.scenario_path: str | None = None
        self.model: AssemblyModel | None = None
        self.scenario = Scenario()
        self.cls: Classifier | None = None
        self.scene: SceneBuilder | None = None
        self.current_step: int | None = None
        self.dirty = False
        self._updating = False
        self._process: QProcess | None = None
        self._pending_scenario: Scenario | None = None
        self._rows: list = []
        self._switched_at = 0.0
        self._entry_camera: dict | None = None   # ракурс, показанный при входе на этап
        self._render_reset = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._do_render)

        self._build_ui()
        self._update_title()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # ---- верхняя панель
        tb = QToolBar("Файл")
        tb.setObjectName("topbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.toggleViewAction().setVisible(False)
        self.addToolBar(tb)
        tb.addWidget(label(f"<span style='color:{theme.RED}'>■</span>&nbsp; ЗАКЛАДКА В ОСНАСТКУ", "brand"))

        def act(text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        act("Открыть сборку NX", self.open_prt, "Ctrl+O", "Открыть сборку NX (.prt) — Ctrl+O")
        act("Открыть сценарий", self.open_scenario, tip="Открыть сохранённый сценарий (.yaml)")
        act("Сохранить", self.save, "Ctrl+S", "Сохранить сценарий — Ctrl+S")
        act("Сохранить как", self.save_as)
        tb.addSeparator()
        act("Обновить из NX", lambda: self.run_nx_export(force=True),
            tip="Заново выгрузить сборку из NX (если меняли подсборки или детали)")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        pdf = act("Экспорт PDF", self.export_pdf, tip="Сформировать PDF-инструкцию по всем этапам")
        tb.widgetForAction(pdf).setObjectName("primaryTool")

        # ---- слева: состав сборки
        left, lv = card()
        lv.addWidget(label("Состав сборки", "cardTitle"))
        lv.addWidget(label("Правый клик — роль компонента или основная деталь оснастки", "hint"))
        self.fixture_label = label("", "chip")
        self.fixture_label.setWordWrap(True)
        lv.addWidget(self.fixture_label)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Компонент", "Роль"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        lv.addWidget(self.tree, 1)

        # ---- 3D
        view, vv = card()
        vv.setContentsMargins(1, 1, 1, 1)
        self.plotter = QtInteractor(self)
        self.plotter.set_background(theme.VIEW_BG_BOTTOM, top=theme.VIEW_BG_TOP)
        self.plotter.add_axes()
        vv.addWidget(self.plotter.interactor)

        # ---- этапы
        steps_w, sv = card()
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        def button(text, slot, tip, name=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            if name:
                b.setObjectName(name)
            b.clicked.connect(slot)
            row1.addWidget(b)
            return b

        self.step_label = label("Этап —", "stepTitle")
        self.step_label.setMinimumWidth(130)
        row1.addWidget(self.step_label)
        self.btn_prev = button("◀  Предыдущий", lambda: self.go_step(-1), "Перейти к предыдущему этапу", "nav")
        self.btn_next = button("Следующий  ▶", lambda: self.go_step(1), "Перейти к следующему этапу", "nav")
        row1.addSpacing(14)
        button("+  Этап в конец", self.add_step, "Добавить новый этап после последнего", "primary")
        self.btn_remove = button("Удалить этап", self.remove_step, "Удалить текущий этап")
        # перестановка и вставка этапов — только из меню заголовка столбца, чтобы не путать с переходом
        row1.addStretch()
        self.show_all = QCheckBox("Показать всю сборку")
        self.show_all.setCursor(Qt.PointingHandCursor)
        self.show_all.toggled.connect(self._show_all_toggled)
        row1.addWidget(self.show_all)
        sv.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(label("Описание", "fieldLabel"))
        self.step_name = QLineEdit()
        self.step_name.setPlaceholderText("Что делать на этапе, например «Установить косынки»")
        self.step_name.textEdited.connect(self._step_name_changed)
        row2.addWidget(self.step_name, 1)
        row2.addSpacing(6)
        row2.addWidget(label("Закладка", "fieldLabel"))
        self.step_dir = QComboBox()
        for code, (title, _) in DIRECTIONS.items():
            self.step_dir.addItem(title, code)
        self.step_dir.setMinimumWidth(170)
        self.step_dir.setFocusPolicy(Qt.StrongFocus)
        self._wheel_filter = NoWheelUnlessFocused(self)
        self.step_dir.installEventFilter(self._wheel_filter)
        self.step_dir.currentIndexChanged.connect(self._step_dir_changed)
        row2.addWidget(self.step_dir)
        row2.addSpacing(6)
        for text, slot, tip in (
                ("Запомнить ракурс", self.save_camera,
                 "Ракурс запоминается и при переходе на другой этап; кнопка — сохранить прямо сейчас"),
                ("Сбросить ракурс", self.reset_camera, "Вернуть общий изометрический вид")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(slot)
            row2.addWidget(b)
        self.camera_mark = label("", "chipMuted")
        row2.addWidget(self.camera_mark)
        sv.addLayout(row2)

        self.table = QTableWidget()
        # без выделения и клавиатуры: этап меняется только щелчком или кнопками
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._header_menu)
        self.table.verticalHeader().setDefaultSectionSize(26)
        sv.addWidget(self.table, 1)
        sv.addWidget(label("Редактируется только текущий этап (▶ в заголовке). Щелчок по другому столбцу — переход "
                           "к этапу. Правый клик по заголовку этапа — вставить, переставить, удалить.", "hint"))

        right = QSplitter(Qt.Vertical)
        right.setHandleWidth(10)
        right.addWidget(view)
        right.addWidget(steps_w)
        right.setSizes([480, 460])

        main = QSplitter(Qt.Horizontal)
        main.setHandleWidth(10)
        main.addWidget(left)
        main.addWidget(right)
        main.setSizes([380, 1120])
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(10, 10, 10, 6)
        wl.addWidget(main)
        self.setCentralWidget(wrap)
        self.statusBar().showMessage("Откройте сборку NX (.prt) или сценарий (.yaml)")

    def _show_all_toggled(self, on):
        if on:
            self._remember_view()
        self.refresh_view(reset_camera=True)

    def _update_title(self):
        name = os.path.basename(self.scenario_path or self.prt_path or "") or "без имени"
        self.setWindowTitle(f"{APP_TITLE} — {name}{' *' if self.dirty else ''}")

    def mark_dirty(self):
        self.dirty = True
        self._update_title()

    # --------------------------------------------------------------- файлы
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, APP_TITLE, "Сохранить изменения сценария?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def open_prt(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Сборка NX", "", "Сборка NX (*.prt)")
        if not path:
            return
        sc_path = scenario_path_for(path)
        if os.path.exists(sc_path):
            r = QMessageBox.question(self, APP_TITLE, f"Найден сценарий {os.path.basename(sc_path)}. Открыть его?")
            if r == QMessageBox.Yes:
                self._load_scenario_file(sc_path)
                return
        self.scenario_path = None
        self._start(path, Scenario(model=path))

    def open_scenario(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Сценарий", "", "Сценарий закладки (*.yaml *.yml)")
        if path:
            self._load_scenario_file(path)

    def _load_scenario_file(self, path):
        try:
            sc = Scenario.load(path)
        except Exception as e:
            QMessageBox.critical(self, APP_TITLE, f"Не удалось прочитать сценарий:\n{e}")
            return
        if not os.path.exists(sc.model):
            QMessageBox.critical(self, APP_TITLE, f"Не найдена сборка:\n{sc.model}")
            return
        self.scenario_path = path
        self._start(sc.model, sc)

    def _start(self, prt, scenario: Scenario):
        self.prt_path = os.path.abspath(prt)
        self.dirty = False
        scenario.model = self.prt_path
        self._pending_scenario = scenario
        if nx_bridge.is_export_fresh(self.prt_path):
            self._on_export_ready()
        else:
            self.run_nx_export()

    def save(self) -> bool:
        if not self.model:
            return False
        if not self.scenario_path:
            return self.save_as()
        self._remember_view()
        self._cleanup_scenario()
        self.scenario.save(self.scenario_path)
        self.dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Сохранено: {self.scenario_path}", 5000)
        return True

    def save_as(self) -> bool:
        if not self.model:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить сценарий", scenario_path_for(self.prt_path),
                                              "Сценарий закладки (*.yaml)")
        if not path:
            return False
        self.scenario_path = path
        return self.save()

    def closeEvent(self, e):
        if self._confirm_discard():
            self.plotter.close()
            e.accept()
        else:
            e.ignore()

    # ------------------------------------------------------------ экспорт NX
    def run_nx_export(self, force=False):
        if not self.prt_path:
            return
        if force and self.model:
            self._pending_scenario = self.scenario
        try:
            cmd = nx_bridge.export_command(self.prt_path)
        except RuntimeError as e:
            QMessageBox.critical(self, APP_TITLE, str(e))
            return
        self._progress = QProgressDialog("Выгрузка сборки из NX…\nЭто может занять до нескольких минут.",
                                         None, 0, 0, self)
        self._progress.setWindowTitle(APP_TITLE)
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.show()
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        self._process.finished.connect(self._on_export_finished)
        self._process.start(cmd[0], cmd[1:])

    def _on_export_finished(self, code, _status):
        out = bytes(self._process.readAll()).decode("cp1251", errors="replace")
        self._progress.close()
        if "EXPORT_OK" not in out:
            QMessageBox.critical(self, APP_TITLE, "Экспорт из NX не удался:\n\n" + out[-2500:])
            return
        self._on_export_ready()

    def _on_export_ready(self):
        try:
            self.model = AssemblyModel(nx_bridge.export_dir_for(self.prt_path))
        except Exception as e:
            QMessageBox.critical(self, APP_TITLE, f"Не удалось прочитать результат экспорта:\n{e}")
            return
        self.scene = SceneBuilder(self.model)
        sc = self._pending_scenario or self.scenario
        self._pending_scenario = None
        if not sc.fixture_main or sc.fixture_main not in self.model.by_key:
            guess = self.model.guess_fixture_main()
            sc.fixture_main = guess.key if guess else None
        if not sc.steps:
            sc.steps.append(Step(name="Установить базовую деталь"))
        self.scenario = sc
        self.dirty = self.dirty or self.scenario_path is None
        self.current_step = None
        self._reclassify()
        self.select_step(0)
        self._update_title()
        if self.model.warnings:
            QMessageBox.warning(self, APP_TITLE, "Предупреждения экспорта:\n" + "\n".join(self.model.warnings[:20]))

    # ------------------------------------------------------------- роли
    def _reclassify(self):
        self.cls = Classifier(self.model, self.scenario.fixture_main, self.scenario.roles)
        self._fill_tree()
        self._fill_table()

    def _fill_tree(self):
        self._updating = True
        self.tree.clear()
        main = self.scenario.fixture_main

        def add(node: Node, parent_item):
            item = QTreeWidgetItem([node.label, ""])
            item.setData(0, KEY_ROLE, node.key)
            role = self.cls.role(node)
            if role:
                item.setText(1, ROLE_TITLES[role] + (" *" if node.key in self.scenario.roles else ""))
                item.setForeground(1, QBrush(QColor(ROLE_COLORS[role]).darker(130)))
            if node.key == main:
                f = QFont()
                f.setBold(True)
                item.setFont(0, f)
                item.setToolTip(0, "Основная деталь оснастки")
            if parent_item is None:
                self.tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            for c in node.children:
                add(c, item)
            return item

        root_item = add(self.model.root, None)
        root_item.setExpanded(True)
        for i in range(root_item.childCount()):
            ch = root_item.child(i)
            ch.setExpanded(ch.data(0, KEY_ROLE) == main)
        self.tree.resizeColumnToContents(1)
        main_node = self.model.by_key.get(main) if main else None
        self.fixture_label.setText(
            f"Оснастка: <b>{main_node.designation}</b>, префикс <b>{self.cls.prefix}</b>" if main_node
            else "<span style='color:#b00'>Основная деталь оснастки не выбрана</span>")
        self._updating = False

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item or not self.model:
            return
        key = item.data(0, KEY_ROLE)
        node = self.model.by_key[key]
        menu = QMenu(self)
        a = menu.addAction("Основная деталь оснастки (префикс %s)" % node.designation.split(".", 1)[0])
        a.triggered.connect(lambda: self._set_fixture_main(key))
        if node.parent is not None:
            menu.addSeparator()
            for role, title in ROLE_TITLES.items():
                a = menu.addAction("Роль: " + title)
                a.setCheckable(True)
                a.setChecked(self.scenario.roles.get(key) == role)
                a.triggered.connect(lambda _=False, r=role: self._set_role(key, r))
            a = menu.addAction("Роль: по умолчанию (по префиксу)")
            a.setEnabled(key in self.scenario.roles)
            a.triggered.connect(lambda: self._set_role(key, None))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_fixture_main(self, key):
        self.scenario.fixture_main = key
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    def _set_role(self, key, role):
        if role is None:
            self.scenario.roles.pop(key, None)
        else:
            self.scenario.roles[key] = role
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    def _cleanup_scenario(self):
        """Убирает из этапов ключи, которые больше не являются строками таблицы."""
        if not self.cls:
            return
        parts = {n.key for n in self.cls.units(PART)}
        movables = {n.key for n in self.cls.units(MOVABLE)}
        for s in self.scenario.steps:
            s.add = [k for k in s.add if k in parts]
            s.show = [k for k in s.show if k in movables]

    # ------------------------------------------------------------- таблица
    def _fill_table(self):
        """Полное перестроение таблицы — только при изменении состава строк или этапов."""
        parts = self.cls.units(PART)
        movables = self.cls.units(MOVABLE)
        self._rows = [("header", "Детали (закладка)")] + [(PART, n) for n in parts] + \
                     [("header", "Прижимы и подвижные элементы (видимость)")] + [(MOVABLE, n) for n in movables]
        t = self.table
        n_steps = len(self.scenario.steps)
        self._updating = True
        t.clear()
        t.setColumnCount(n_steps)
        t.setRowCount(len(self._rows))
        bold = QFont()
        bold.setBold(True)
        labels = []
        for r, (kind, obj) in enumerate(self._rows):
            labels.append(obj if kind == "header" else obj.label)
            for c in range(n_steps):
                it = QTableWidgetItem()
                it.setFlags(Qt.ItemIsEnabled if kind != "header" else Qt.NoItemFlags)
                if kind == "header":
                    it.setBackground(QColor(theme.CELL_GROUP))
                else:
                    it.setData(KEY_ROLE, obj.key)
                    it.setData(KEY_KIND, kind)
                t.setItem(r, c, it)
        t.setVerticalHeaderLabels(labels)
        for r, (kind, _) in enumerate(self._rows):
            if kind == "header":
                t.verticalHeaderItem(r).setFont(bold)
        t.horizontalHeader().setDefaultSectionSize(140)
        self._updating = False
        self._sync_table()

    def _sync_table(self):
        """Галочки, цвета, заголовки по текущему сценарию и этапу (без перестроения)."""
        t = self.table
        steps = self.scenario.steps
        cur = self.current_step
        if t.columnCount() != len(steps):
            return self._fill_table()
        self._updating = True
        bold, normal = QFont(), QFont()
        bold.setBold(True)
        for c in range(len(steps)):
            h = t.horizontalHeaderItem(c) or QTableWidgetItem()
            h.setText(("▶ " if c == cur else "") + self._step_header(c))
            h.setFont(bold if c == cur else normal)
            h.setToolTip(steps[c].name)
            t.setHorizontalHeaderItem(c, h)
        part_steps = {}
        for i, st in enumerate(steps):
            for k in st.add:
                part_steps.setdefault(k, i)
        unassigned = 0
        for r, (kind, obj) in enumerate(self._rows):
            if kind == "header":
                for c in range(len(steps)):
                    t.item(r, c).setBackground(QColor(theme.CELL_GROUP_CURRENT if c == cur else theme.CELL_GROUP))
                continue
            part_step = part_steps.get(obj.key) if kind == PART else None
            for c, st in enumerate(steps):
                it = t.item(r, c)
                checked = obj.key in (st.add if kind == PART else st.show)
                it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                locked = kind == PART and part_step is not None and part_step != c
                if c == cur:
                    if checked:
                        bg = theme.CELL_PART_ON if kind == PART else theme.CELL_MOV_ON
                    else:
                        bg = theme.CELL_LOCKED if locked else theme.CELL_CURRENT
                    tip = f"Деталь закладывается на этапе {part_step + 1}" if locked else "Щёлкните, чтобы отметить"
                else:
                    bg = (theme.CELL_PART_ON_OTHER if kind == PART else theme.CELL_MOV_ON_OTHER) if checked \
                        else theme.CELL_OTHER
                    tip = f"Щёлкните, чтобы перейти к этапу {c + 1}"
                it.setBackground(QColor(bg))
                it.setToolTip(tip)
            vh = t.verticalHeaderItem(r)
            if kind == PART and part_step is None:
                unassigned += 1
                vh.setForeground(QBrush(QColor(theme.RED)))
                vh.setToolTip("Деталь не заложена ни на одном этапе")
            else:
                vh.setForeground(QBrush(QColor(theme.TEXT)))
                vh.setToolTip("")
        n_parts = sum(1 for k, _ in self._rows if k == PART)
        n_mov = sum(1 for k, _ in self._rows if k == MOVABLE)
        self.statusBar().showMessage(
            f"Деталей: {n_parts}, не распределено: {unassigned}. Прижимов/подвижных: {n_mov}. "
            f"Этапов: {len(steps)}.")
        self._updating = False
        self._update_step_buttons()

    def _update_step_buttons(self):
        n = len(self.scenario.steps) if self.model else 0
        i = self.current_step if self.current_step is not None else -1
        self.btn_prev.setEnabled(n > 0 and i > 0)
        self.btn_next.setEnabled(n > 0 and 0 <= i < n - 1)
        self.btn_remove.setEnabled(n > 1 and i >= 0)

    def _step_header(self, i):
        name = self.scenario.steps[i].name
        short = (name[:16] + "…") if len(name) > 17 else name
        return f"{i + 1}" + (f"\n{short}" if short else "")

    def _header_menu(self, pos):
        """Правый клик по заголовку этапа: вставка, перестановка, удаление."""
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        if col < 0 or not self.model:
            return
        if col != self.current_step:
            self.select_step(col)
        n = len(self.scenario.steps)
        menu = QMenu(self)
        menu.addAction(f"Вставить новый этап после этапа {col + 1}").triggered.connect(self.insert_step)
        menu.addSeparator()
        a = menu.addAction(f"Поменять этап {col + 1} местами с этапом {col}")
        a.setEnabled(col > 0)
        a.triggered.connect(lambda: self.move_step(-1))
        a = menu.addAction(f"Поменять этап {col + 1} местами с этапом {col + 2}")
        a.setEnabled(col < n - 1)
        a.triggered.connect(lambda: self.move_step(1))
        menu.addSeparator()
        a = menu.addAction(f"Удалить этап {col + 1}")
        a.setEnabled(n > 1)
        a.triggered.connect(self.remove_step)
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _header_clicked(self, col):
        if col != self.current_step:
            self.select_step(col)
            self._switched_at = time.monotonic()

    def _cell_clicked(self, row, col):
        item = self.table.item(row, col)
        if item is None or item.data(KEY_KIND) not in (PART, MOVABLE):
            return
        if col != self.current_step:
            # щелчок по другому этапу — только переход, ничего не отмечаем
            self.select_step(col)
            self._switched_at = time.monotonic()
            return
        if time.monotonic() - self._switched_at < SWITCH_GUARD_SEC:
            return  # второй щелчок двойного щелчка
        key, kind = item.data(KEY_ROLE), item.data(KEY_KIND)
        step = self.scenario.steps[col]
        if kind == PART:
            other = self.scenario.step_of_part(key)
            if other is not None and other != col:
                self.statusBar().showMessage(f"Деталь уже закладывается на этапе {other + 1}. "
                                             f"Перейдите на него и снимите отметку.", 5000)
                return
            lst = step.add
        else:
            lst = step.show
        if key in lst:
            lst.remove(key)
        else:
            lst.append(key)
        self.mark_dirty()
        self._sync_table()
        self.refresh_view()

    # ------------------------------------------------------------- этапы
    def select_step(self, idx):
        if not self.model or idx is None or idx < 0 or idx >= len(self.scenario.steps):
            return
        changed = idx != self.current_step
        if changed:
            self._remember_view()
        self.current_step = idx
        s = self.scenario.steps[idx]
        self._updating = True
        self.step_label.setText(f"Этап {idx + 1} из {len(self.scenario.steps)}")
        self.step_name.setText(s.name)
        self.step_dir.setCurrentIndex(max(0, self.step_dir.findData(s.direction)))
        self._updating = False
        self._update_camera_mark()
        self._sync_table()
        self.refresh_view(reset_camera=changed)

    def go_step(self, delta):
        if self.current_step is None:
            return
        target = self.current_step + delta
        if 0 <= target < len(self.scenario.steps):
            self.select_step(target)

    def add_step(self):
        """Новый этап в конец сценария (прижимы — как на последнем этапе)."""
        self._insert_step(len(self.scenario.steps))

    def insert_step(self):
        """Новый этап сразу после текущего; следующие этапы сдвигаются."""
        cur = self.current_step
        self._insert_step(cur + 1 if cur is not None else len(self.scenario.steps))

    def _insert_step(self, at):
        if not self.model:
            return
        steps = self.scenario.steps
        prev = steps[at - 1] if at > 0 and steps else None
        self._remember_view()
        steps.insert(at, Step(show=list(prev.show) if prev else [], direction=prev.direction if prev else "-Z"))
        self.current_step = None
        self.mark_dirty()
        self._fill_table()
        self.select_step(at)
        self.step_name.setFocus()

    def remove_step(self):
        if not self.model or self.current_step is None or len(self.scenario.steps) <= 1:
            return
        s = self.scenario.steps[self.current_step]
        if s.add and QMessageBox.question(
                self, APP_TITLE, f"На этапе {self.current_step + 1} закладываются детали. Удалить этап?") \
                != QMessageBox.Yes:
            return
        idx = min(self.current_step, len(self.scenario.steps) - 2)
        del self.scenario.steps[self.current_step]
        self._entry_camera = None  # вид удалённого этапа не переносим
        self.current_step = None
        self.mark_dirty()
        self._fill_table()
        self.select_step(idx)

    def move_step(self, delta):
        i = self.current_step
        steps = self.scenario.steps
        if i is None or not (0 <= i + delta < len(steps)):
            return
        self._remember_view()
        steps[i], steps[i + delta] = steps[i + delta], steps[i]
        self.mark_dirty()
        self.current_step = None  # чтобы select_step перерисовал таблицу и вид
        self.select_step(i + delta)

    def _step_name_changed(self, text):
        if self._updating or self.current_step is None:
            return
        self.scenario.steps[self.current_step].name = text
        self.table.horizontalHeaderItem(self.current_step).setText("▶ " + self._step_header(self.current_step))
        self.mark_dirty()
        self.refresh_view()

    def _step_dir_changed(self, _):
        if self._updating or self.current_step is None:
            return
        self.scenario.steps[self.current_step].direction = self.step_dir.currentData()
        self.mark_dirty()
        self.refresh_view()

    def _update_camera_mark(self):
        s = self.scenario.steps[self.current_step] if self.current_step is not None else None
        self.camera_mark.setText("ракурс сохранён" if s and s.camera else "ракурс по умолчанию")

    def _remember_view(self):
        """Если вид текущего этапа повернули — сохранить его за этапом."""
        if self.current_step is None or self._entry_camera is None or self.show_all.isChecked():
            return
        if self._render_timer.isActive() or self.current_step >= len(self.scenario.steps):
            return  # этап ещё не отрисован — вид не его
        cam = SceneBuilder.capture_camera(self.plotter)
        if cam != self._entry_camera:
            self.scenario.steps[self.current_step].camera = cam
            self._entry_camera = cam
            self.mark_dirty()

    def save_camera(self):
        if self.current_step is None:
            return
        cam = SceneBuilder.capture_camera(self.plotter)
        self.scenario.steps[self.current_step].camera = cam
        self._entry_camera = cam
        self._update_camera_mark()
        self.mark_dirty()

    def reset_camera(self):
        if self.current_step is None:
            return
        self.scenario.steps[self.current_step].camera = None
        self._update_camera_mark()
        self.mark_dirty()
        self.refresh_view(reset_camera=True)

    # ------------------------------------------------------------- 3D
    def refresh_view(self, reset_camera=False):
        """Отрисовка откладывается до конца обработки событий: при быстрых переходах рисуется только последний этап."""
        if not self.scene:
            return
        self._render_reset = self._render_reset or reset_camera
        self._render_timer.start(0)

    def _do_render(self):
        reset, self._render_reset = self._render_reset, False
        step = None if self.show_all.isChecked() else self.current_step
        if step is not None and step >= len(self.scenario.steps):
            step = None
        self.scene.build(self.plotter, self.scenario, self.cls, step, set_camera=reset)
        self.plotter.set_background(theme.VIEW_BG_BOTTOM, top=theme.VIEW_BG_TOP)
        self.plotter.add_axes()
        if step is None:
            title = "Вся сборка"
        else:
            name = self.scenario.steps[step].name
            title = f"Этап {step + 1} из {len(self.scenario.steps)}" + (f" — {name}" if name else "")
        kw = {"font_file": ARIAL} if os.path.exists(ARIAL) else {}
        self.plotter.add_text(title, position="upper_left", font_size=10, color=theme.GRAPHITE, **kw)
        self.plotter.render()
        if reset and step is not None:
            self._entry_camera = SceneBuilder.capture_camera(self.plotter)
            self._update_camera_mark()

    # ------------------------------------------------------------- PDF
    def export_pdf(self):
        if not self.model:
            return
        base = os.path.splitext(self.scenario_path or self.prt_path)[0]
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт PDF", base + ".pdf", "PDF (*.pdf)")
        if not path:
            return
        self._remember_view()
        self._update_camera_mark()
        self._cleanup_scenario()
        dlg = QProgressDialog("Формирование PDF…", None, 0, len(self.scenario.steps), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.show()

        def progress(i, n):
            dlg.setValue(i)
            QApplication.processEvents()

        try:
            export_pdf(path, self.scenario, self.cls, self.scene, title=os.path.basename(self.prt_path),
                       progress=progress)
        except PermissionError:
            dlg.close()
            QMessageBox.critical(self, APP_TITLE, f"Файл занят (открыт в другой программе?):\n{path}")
            return
        dlg.close()
        self.statusBar().showMessage(f"PDF сохранён: {path}", 8000)
        os.startfile(path)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    try:  # светлая тема независимо от настроек Windows: цвета рассчитаны на неё
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    theme.apply(app)
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.lower().endswith((".yaml", ".yml")):
            w._load_scenario_file(arg)
        elif arg.lower().endswith(".prt"):
            w._start(arg, Scenario(model=arg))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
