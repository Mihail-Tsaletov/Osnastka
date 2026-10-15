"""Десктоп-приложение: настройка сценария закладки и экспорт PDF."""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time

from qtpy.QtCore import QEvent, QObject, QProcess, Qt, QTimer, Signal
from qtpy.QtGui import QAction, QBrush, QColor, QFont, QIcon, QKeySequence, QPixmap
from qtpy.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QInputDialog,                                QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
                               QProgressDialog, QPushButton, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem,
                               QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from pyvistaqt import QtInteractor

from . import __version__, nx_bridge, nx_link, paths, replace, theme, updater
from .model import (FIXTURE, GROUP_PREFIX, HIDDEN, MOVABLE, PART, ROLE_TITLES, AssemblyModel, Classifier, Node,
                    prefix_of)
from .pdf_report import export_pdf
from . import details as lay
from .scenario import DIRECTIONS, Detail, Scenario, Step, detail_letter, find_for_model
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


class UpdateSignals(QObject):
    """Результат фоновой проверки обновлений → в поток интерфейса."""
    result = Signal(object, bool)   # (вид, значение), ручная проверка
    error = Signal(str, bool)


def scenario_path_for(prt: str) -> str:
    """Путь для нового сценария: «Документы\\Osnastka\\Сценарии» (папка модели может быть только для чтения)."""
    return paths.new_scenario_path(nx_link.model_stem(prt))


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
        self._sel_timer = QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.timeout.connect(self._apply_selection)
        self._fresh_export = False
        self._wheel_filter_global = NoWheelUnlessFocused(self)

        self._pending_update = None
        self._await_replace = None
        self._editing_detail = None   # (этап, № уточнения, новое) — вид уточнения настраивается в 3D
        self._detail_back = None
        self._upd = UpdateSignals(self)
        self._upd.result.connect(self._on_update_result)
        self._upd.error.connect(self._on_update_error)

        self._build_ui()
        self._update_title()
        QTimer.singleShot(2500, lambda: self.check_updates(manual=False))

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
        act("Окно выбора", self.open_picker,
            tip="Отдельное окно с моделью: щелчок по детали выбирает её в списке")
        act("Кнопка в NX", self.show_nx_button_help,
            tip="Как передавать модель прямо из открытого NX (в том числе из Teamcenter)")
        act("Обновить из NX", lambda: self.run_nx_export(force=True),
            tip="Заново выгрузить сборку из NX (если меняли подсборки или детали)")
        act("Обновления", lambda: self.check_updates(manual=True),
            tip=f"Версия {__version__}. Проверить, есть ли новая версия программы")
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        pdf = act("Экспорт PDF", self.export_pdf, tip="Сформировать PDF-инструкцию по всем этапам")
        tb.widgetForAction(pdf).setObjectName("primaryTool")

        # ---- слева: состав сборки
        left, lv = card()
        lv.addWidget(label("Состав сборки", "cardTitle"))
        tree_hint = label("Выделите один или несколько элементов (Ctrl, Shift) и щёлкните правой кнопкой: роль, "
                          "группа, основная деталь оснастки. Выбранное подсвечивается синим, двойной щелчок — "
                          "приблизить.", "hint")
        tree_hint.setWordWrap(True)
        lv.addWidget(tree_hint)
        self.fixture_label = label("", "chip")
        self.fixture_label.setWordWrap(True)
        lv.addWidget(self.fixture_label)
        self.missing_label = label("", "chipMuted")
        self.missing_label.setWordWrap(True)
        self.missing_label.hide()
        lv.addWidget(self.missing_label)
        self.tree = QTreeWidget()
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(lambda: self._sel_timer.start(80))
        self.tree.itemDoubleClicked.connect(lambda *_: self.focus_selection())
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
        self.color_mode = QComboBox()
        self.color_mode.addItem("Цвета: по ролям", "roles")
        self.color_mode.addItem("Цвета: как в NX", "nx")
        self.color_mode.setToolTip("«Как в NX» — оснастка и детали своими цветами из NX; закладываемая деталь, "
                                   "стрелки и убираемое — цветами инструкции. Действует и на PDF.")
        self.color_mode.setFocusPolicy(Qt.StrongFocus)
        self.color_mode.installEventFilter(self._wheel_filter_global)
        self.color_mode.currentIndexChanged.connect(self._color_mode_changed)
        row1.addWidget(self.color_mode)
        row1.addSpacing(10)
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

        row3 = QHBoxLayout()
        row3.setSpacing(6)
        row3.addWidget(label("Уточнения на листе", "fieldLabel"))
        self.details_box = QHBoxLayout()
        self.details_box.setSpacing(6)
        row3.addLayout(self.details_box)
        self.btn_add_detail = QPushButton("+  Уточнение")
        self.btn_add_detail.setToolTip(
            "Окошко с крупным видом на листе этапа. Выделите элемент в дереве или таблице — вид наведётся на него; "
            "без выделения — настройте вид в 3D сами и нажмите «Готово»")
        self.btn_add_detail.clicked.connect(self.add_detail)
        self.btn_layout = QPushButton("Компоновка листа…")
        self.btn_layout.setToolTip("Лист этапа как в PDF: перетащите окошки уточнений, обводки и стрелки деталей "
                                   "и прижимов")
        self.btn_layout.clicked.connect(lambda: self.open_layout())
        self.detail_edit_label = label("", "chip")
        self.btn_detail_ok = QPushButton("Готово")
        self.btn_detail_ok.setObjectName("primary")
        self.btn_detail_ok.clicked.connect(lambda: self._finish_detail_edit(True, layout=True))
        self.btn_detail_cancel = QPushButton("Отмена")
        self.btn_detail_cancel.clicked.connect(lambda: self._finish_detail_edit(False))
        for w in (self.btn_add_detail, self.btn_layout, self.detail_edit_label, self.btn_detail_ok,
                  self.btn_detail_cancel):
            if isinstance(w, QPushButton):
                w.setCursor(Qt.PointingHandCursor)
            row3.addWidget(w)
        row3.addStretch()
        sv.addLayout(row3)

        self.table = QTableWidget()
        # без выделения и клавиатуры: этап меняется только щелчком или кнопками
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._cell_menu)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._header_menu)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.verticalHeader().sectionClicked.connect(self._row_header_clicked)
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
        self.update_btn = QPushButton()
        self.update_btn.setObjectName("primary")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self.install_update)
        self.update_btn.hide()
        self.statusBar().addPermanentWidget(self.update_btn)
        self.await_label = label("", "chip")
        self.await_cancel = QPushButton("Отменить ожидание")
        self.await_cancel.clicked.connect(lambda: self._set_await_replace(None))
        self.statusBar().addPermanentWidget(self.await_label)
        self.statusBar().addPermanentWidget(self.await_cancel)
        self.await_label.hide()
        self.await_cancel.hide()
        self.statusBar().addPermanentWidget(label(f"версия {__version__}", "hint"))
        self.statusBar().showMessage("Откройте сборку NX (.prt) или сценарий (.yaml)")

    def _color_mode_changed(self, _):
        if self._updating or not self.model:
            return
        self.scenario.color_mode = self.color_mode.currentData()
        self.mark_dirty()
        self.refresh_view()

    def _show_all_toggled(self, on):
        self._finish_detail_edit(True)
        self._update_details_bar()
        if on:
            self._remember_view()
        self.refresh_view(reset_camera=True)

    def _update_title(self):
        name = (os.path.basename(self.scenario_path) if self.scenario_path
                else nx_link.model_title(self.prt_path or "")) or "без имени"
        self.setWindowTitle(f"{APP_TITLE} {__version__} — {name}{' *' if self.dirty else ''}")

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
        self._open_model(path)

    def _open_model(self, source, intro=""):
        """Открыть модель: если для неё есть сценарии — предложить открыть один из них."""
        found = find_for_model(source)
        if found:
            new = "Новый сценарий"
            items = [f"{os.path.basename(p)} — " + ("та же модель" if exact else
                                                    "модель с таким же именем из другой папки")
                     for p, exact in found] + [new]
            if len(found) == 1 and found[0][1]:
                r = QMessageBox.question(self, APP_TITLE, intro + f"Для этой модели есть сценарий "
                                         f"«{os.path.basename(found[0][0])}». Открыть его?\n\n«Нет» — новый сценарий.")
                choice = items[0] if r == QMessageBox.Yes else new
            else:
                choice, ok = QInputDialog.getItem(self, APP_TITLE, intro + "Для этой модели найдены сценарии:",
                                                  items, 0, False)
                if not ok:
                    return
            if choice != new:
                path = found[items.index(choice)][0]
                self._load_scenario_file(path, model_override=source)
                return
        self.scenario_path = None
        self._start(source, Scenario(model=source))

    def open_scenario(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Сценарий", "", "Сценарий закладки (*.yaml *.yml)")
        if path:
            self._load_scenario_file(path)

    def _load_scenario_file(self, path, model_override=None):
        try:
            sc = Scenario.load(path)
        except Exception as e:
            QMessageBox.critical(self, APP_TITLE, f"Не удалось прочитать сценарий:\n{e}")
            return
        if model_override:   # сценарий к модели с тем же именем, открытой из другой папки
            sc.model = model_override
        exists = nx_bridge.is_export_fresh(sc.model) if nx_link.is_tc(sc.model) else os.path.exists(sc.model)
        if not exists:
            QMessageBox.critical(self, APP_TITLE, f"Не найдена сборка:\n{nx_link.model_title(sc.model)}\n\n"
                                                  f"Для модели из Teamcenter откройте её в NX и нажмите кнопку Osnastka.")
            return
        self.scenario_path = path
        self._start(sc.model, sc)

    # ------------------------------------------------------------ модель из открытого NX
    def _watch_inbox(self):
        from qtpy.QtCore import QFileSystemWatcher
        self._inbox_watcher = QFileSystemWatcher([nx_link.INBOX], self)
        self._inbox_watcher.directoryChanged.connect(lambda *_: QTimer.singleShot(300, self.take_nx_request))
        QTimer.singleShot(500, self.take_nx_request)   # запрос, пришедший до запуска программы

    def take_nx_request(self):
        if not self.isVisible():   # закрытое окно запросы не забирает
            return
        req = nx_link.take_request()
        if not req:
            return
        self.raise_()
        self.activateWindow()
        source = req.get("source", "")
        awaiting = self._await_replace if self.model and self._await_replace in self.model.by_key else None
        if not awaiting and not self._confirm_discard():
            return
        # выгрузка из сеанса NX лежит во временной папке — перенести в кэш программы
        src_dir, dst_dir = req.get("export_dir", ""), nx_bridge.export_dir_for(source)
        if src_dir and os.path.isdir(src_dir) and paths.norm(src_dir) != paths.norm(dst_dir):
            shutil.rmtree(dst_dir, ignore_errors=True)
            os.makedirs(os.path.dirname(dst_dir), exist_ok=True)
            shutil.move(src_dir, dst_dir)
        if awaiting:   # ждали деталь из NX для замены шоп-детали
            self._set_await_replace(None)
            self._replace_finish(awaiting, source)
            return
        self._open_model(source, intro=f"Из NX пришла модель «{nx_link.model_title(source)}».\n\n")

    def _set_await_replace(self, key):
        """Ожидание детали из открытого NX для замены узла key (None — отменить)."""
        self._await_replace = key
        if key:
            node = self.model.by_key[key]
            self.await_label.setText(f"Ждём из NX деталь для замены «{node.short_label}» — откройте её в NX и "
                                     f"нажмите кнопку Osnastka")
            self.await_label.show()
            self.await_cancel.show()
        else:
            self.await_label.hide()
            self.await_cancel.hide()

    def _replace_from_nx(self, node):
        self._set_await_replace(node.key)
        QMessageBox.information(
            self, APP_TITLE,
            f"Откройте в NX нормальную сборку (или деталь) для «{node.short_label}» — с диска или из Teamcenter — "
            f"и нажмите кнопку Osnastka (Alt+F8 → журнал osnastka_nx.cs → «Выполнить»).\n\n"
            f"Программа совместит её с «{node.short_label}» по геометрии и подставит вместо неё. "
            f"Пока ждём — внизу окна надпись; отменить — кнопкой рядом с ней.")

    def show_nx_button_help(self):
        QApplication.clipboard().setText(nx_link.BUTTON_JOURNAL)
        QMessageBox.information(
            self, APP_TITLE,
            "Модель можно передать прямо из открытого NX — и из обычного, и из NX с Teamcenter "
            "(режим определяется сам, сохранять модель локально не нужно).\n\n"
            "Журнал для кнопки (C#; путь уже скопирован в буфер обмена):\n"
            f"{nx_link.BUTTON_JOURNAL}\n"
            "Это копия журнала из папки программы, которая обновляется при каждом запуске программы, — "
            "поэтому кнопку в NX настраивать на неё (а не на папку программы): так после обновлений "
            "программы NX сразу работает с новым журналом.\n\n"
            "Быстрый способ: в NX нажмите Alt+F8 («Воспроизвести журнал»; или вкладка «Разработка» → "
            "«Журнал» → «Воспроизвести»), нажмите «Обзор», выберите этот файл и «Выполнить». "
            "NX запоминает последний журнал — в следующий раз только Alt+F8 → «Выполнить».\n\n"
            "Постоянная кнопка: в NX «Настройка» (Ctrl+1) → вкладка «Команды» → категория «Новый элемент» → "
            "перетащите «Новая пользовательская команда» на ленту → правый клик по ней → «Изменить действие» → "
            "тип «Журнал» → вставьте путь из буфера обмена.\n\n"
            "Нажали кнопку в NX — модель выгрузится и откроется здесь. После изменений в NX нажмите её снова.")

    def _start(self, prt, scenario: Scenario):
        self.prt_path = prt if nx_link.is_tc(prt) else os.path.abspath(prt)
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
        try:
            self.scenario.save(self.scenario_path)
        except OSError as e:
            QMessageBox.warning(self, APP_TITLE, f"Не удалось сохранить сценарий (папка только для чтения?):\n"
                                                 f"{self.scenario_path}\n{e}\n\nВыберите другое место.")
            self.scenario_path = None
            return self.save_as()
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

    # ------------------------------------------------------------ обновления
    def check_updates(self, manual=False):
        """manual — кнопка «Обновления» (окно обновлений); иначе тихая проверка при запуске."""
        if manual:
            self.show_update_dialog()
            return

        def work():
            try:
                if updater.is_git_checkout():
                    self._upd.result.emit(("git", updater.git_check()), False)
                else:
                    self._upd.result.emit(("package", updater.check()), False)
            except Exception as e:
                self._upd.error.emit(str(e), False)

        threading.Thread(target=work, daemon=True).start()

    def _on_update_result(self, res, manual):
        kind, val = res
        if (kind == "git" and val > 0) or (kind == "package" and val is not None):
            self._pending_update = res
            self.update_btn.setText(f"Есть обновление ({val} изм.)" if kind == "git"
                                    else f"Доступно обновление {val.version} — установить")
            self.update_btn.show()

    def _on_update_error(self, msg, manual):
        pass   # тихая проверка: нет сети / нет папки — не мешаем работе

    def install_update(self):
        self.show_update_dialog()

    def show_update_dialog(self):
        from qtpy.QtWidgets import QDialog
        if updater.is_git_checkout():
            n = updater.git_check()
            if n <= 0:
                QMessageBox.information(self, APP_TITLE, f"Версия {__version__} (из исходников, git): обновлений нет.")
                return
            if QMessageBox.question(self, APP_TITLE, f"На сервере {n} новых изменений. Загрузить (git pull)?") \
                    == QMessageBox.Yes:
                try:
                    out = updater.git_pull()
                    QMessageBox.information(self, APP_TITLE, f"Обновлено:\n{out}\n\nПерезапустите программу.")
                except Exception as e:
                    QMessageBox.critical(self, APP_TITLE, f"git pull не удался:\n{e}")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Обновления — " + APP_TITLE)
        dlg.setMinimumWidth(620)
        lay = QVBoxLayout(dlg)
        built_version, _ = updater.build_info()
        where = "обновление, установленное из архива" if updater.running_from_update() else "встроенная в программу"
        lay.addWidget(label(f"Работает версия <b>{__version__}</b> ({where}). Установленная программа — "
                            f"{built_version}.", None))
        hint = label("Чтобы обновить программу без прав администратора, положите архив "
                     "<b>Osnastka-update-&lt;версия&gt;.zip</b> в папку обновлений и нажмите «Установить». "
                     "Программа перезапустится сама, другие программы не запускаются.", "hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        folder_row = QHBoxLayout()
        folder_label = label("", "chipMuted")
        folder_label.setWordWrap(True)
        folder_row.addWidget(folder_label, 1)
        b_change = QPushButton("Изменить папку…")
        b_open = QPushButton("Открыть папку")
        folder_row.addWidget(b_change)
        folder_row.addWidget(b_open)
        lay.addLayout(folder_row)
        found_label = label("", None)
        found_label.setWordWrap(True)
        lay.addWidget(found_label)

        buttons = QHBoxLayout()
        b_install = QPushButton("Установить")
        b_install.setObjectName("primary")
        b_file = QPushButton("Выбрать файл обновления…")
        b_github = QPushButton("Проверить GitHub")
        b_revert = QPushButton("Вернуть встроенную версию")
        b_close = QPushButton("Закрыть")
        for b in (b_install, b_file, b_github, b_revert):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(b_close)
        lay.addLayout(buttons)
        state = {"pkg": None}

        def refresh():
            folder = updater.update_folder()
            folder_label.setText(f"Папка обновлений: {folder}")
            current = updater.parse_version(__version__)
            pkgs = updater.folder_packages(folder)
            newer = [p for p in pkgs if updater.parse_version(p.version) > current]
            state["pkg"] = newer[0] if newer else None
            if newer:
                p = newer[0]
                found_label.setText(f"Найдено обновление <b>{p.version}</b>: {os.path.basename(p.path)}"
                                    + (f"<br>{p.notes}" if p.notes else ""))
            elif pkgs:
                found_label.setText(f"В папке есть архивы, но не новее работающей версии (последний — {pkgs[0].version}).")
            else:
                found_label.setText("В папке обновлений нет архивов Osnastka-update-…zip.")
            b_install.setEnabled(state["pkg"] is not None)
            b_revert.setEnabled(os.path.exists(updater.CURRENT))

        def change_folder():
            d = QFileDialog.getExistingDirectory(dlg, "Папка с обновлениями", updater.update_folder())
            if d:
                updater.set_update_folder(d)
                refresh()

        def choose_file():
            path, _ = QFileDialog.getOpenFileName(dlg, "Архив обновления", updater.update_folder(),
                                                  "Обновление программы (Osnastka-update-*.zip);;Архивы (*.zip)")
            if path:
                self._install_package(path, dlg)

        def github():
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                p = updater.github_package()
            except Exception as e:
                p, err = None, str(e)
            else:
                err = ""
            finally:
                QApplication.restoreOverrideCursor()
            if p and updater.parse_version(p.version) > updater.parse_version(__version__):
                if QMessageBox.question(dlg, APP_TITLE, f"На GitHub есть версия {p.version}. Скачать и установить?") \
                        == QMessageBox.Yes:
                    try:
                        self._install_package(updater.download(p), dlg)
                    except Exception as e:
                        QMessageBox.critical(dlg, APP_TITLE, f"Не удалось скачать обновление:\n{e}")
            else:
                QMessageBox.information(dlg, APP_TITLE, "GitHub недоступен (нет доступа в интернет?)." if err
                                        else "На GitHub нет версии новее работающей.")

        def revert():
            if QMessageBox.question(dlg, APP_TITLE, f"Отключить установленные обновления и вернуться к версии "
                                                    f"{built_version}, встроенной в программу?") == QMessageBox.Yes:
                updater.revert_to_builtin()
                self._offer_restart(dlg, f"Со следующего запуска будет работать версия {built_version}.")

        b_change.clicked.connect(change_folder)
        b_open.clicked.connect(lambda: os.startfile(updater.update_folder()))
        b_install.clicked.connect(lambda: state["pkg"] and self._install_package(state["pkg"].path, dlg))
        b_file.clicked.connect(choose_file)
        b_github.clicked.connect(github)
        b_revert.clicked.connect(revert)
        b_close.clicked.connect(dlg.reject)
        refresh()
        (getattr(dlg, "exec", None) or dlg.exec_)()

    def _install_package(self, zip_path, parent):
        if not updater.FROZEN:
            QMessageBox.information(parent, APP_TITLE, "Программа запущена из исходников — обновление архивом "
                                                       "работает только в установленной программе (Osnastka.exe).")
            return
        try:
            pkg = updater.install(zip_path)
        except Exception as e:
            QMessageBox.critical(parent, APP_TITLE, f"Обновление не установлено:\n{e}")
            return
        self.update_btn.hide()
        self._pending_update = None
        self._offer_restart(parent, f"Обновление {pkg.version} установлено.")

    def _offer_restart(self, parent, text):
        if QMessageBox.question(parent, APP_TITLE, text + "\n\nПерезапустить программу сейчас?") != QMessageBox.Yes:
            return
        if not self._confirm_discard():
            return
        cmd = updater.restart_command()
        QProcess.startDetached(cmd[0], cmd[1:])
        self.dirty = False
        parent.close()
        self.close()
        QApplication.quit()

    def closeEvent(self, e):
        if self._confirm_discard():
            if getattr(self, "picker", None) is not None:
                self.picker.plotter.close()
                self.picker.deleteLater()
            self.plotter.close()
            e.accept()
        else:
            e.ignore()

    # ------------------------------------------------------------ экспорт NX
    def run_nx_export(self, force=False, path=None, on_done=None):
        """Выгрузка файла из NX в фоне. path — другой файл (замена узла), on_done — что сделать после."""
        target = path or self.prt_path
        if not target:
            return
        if force and self.model:
            self._pending_scenario = self.scenario
        try:
            cmd = nx_bridge.export_command(target)
        except RuntimeError as e:
            QMessageBox.critical(self, APP_TITLE, str(e))
            return
        self._export_on_done = on_done
        self._export_target, self._export_started = target, time.time()
        self._progress = QProgressDialog(f"Выгрузка из NX: {os.path.basename(target)}…\n"
                                         f"Это может занять до нескольких минут.", None, 0, 0, self)
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
        if not nx_bridge.export_succeeded(self._export_target, out, self._export_started):
            QMessageBox.critical(self, APP_TITLE, "Экспорт из NX не удался:\n\n" + out[-2500:])
            return
        on_done, self._export_on_done = getattr(self, "_export_on_done", None), None
        if on_done:
            on_done()
            return
        self._fresh_export = True
        self._on_export_ready()

    def _on_export_ready(self):
        sc = self._pending_scenario or self.scenario
        stale = [r["file"] for r in sc.replacements.values()
                 if os.path.exists(r.get("file", "")) and not nx_bridge.is_export_fresh(r["file"])]
        if stale:  # сначала выгрузить файлы замен узлов, потом продолжить открытие
            self.run_nx_export(path=stale[0], on_done=self._on_export_ready)
            return
        try:
            self._set_await_replace(None)   # ожидание детали из NX относилось к прежней модели
            self._editing_detail = None
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
        self._updating = True
        self.color_mode.setCurrentIndex(max(0, self.color_mode.findData(sc.color_mode)))
        self._updating = False
        repl_warnings = replace.apply_all(self.model, sc)
        skipped = self.model.apply_groups(sc.groups)
        self.dirty = self.dirty or self.scenario_path is None
        self.current_step = None
        self._reclassify()
        self.select_step(0)
        self._update_title()
        if getattr(self, "picker", None) is not None and self.picker.isVisible():
            self.picker.setWindowTitle(f"Выбор по модели — {nx_link.model_title(self.prt_path)}")
            self.picker.rebuild(reset_camera=True)
        fresh, self._fresh_export = self._fresh_export, False
        if fresh and self.model.missing:
            QMessageBox.information(self, APP_TITLE, self._missing_text(20))
        if self.model.warnings:
            QMessageBox.warning(self, APP_TITLE, "Предупреждения экспорта:\n" + "\n".join(self.model.warnings[:20]))
        if skipped:
            QMessageBox.warning(self, APP_TITLE, "Группы без тел в текущей модели (пропущены): " + ", ".join(skipped))
        if repl_warnings:
            QMessageBox.warning(self, APP_TITLE, "Замены узлов:\n" + "\n".join(repl_warnings))

    def _missing_text(self, limit) -> str:
        m = self.model.missing
        lines = [f"  {x['count']} × {x['name']}" for x in m[:limit]]
        more = f"\n  … и ещё {len(m) - limit} видов" if len(m) > limit else ""
        return (f"Сборка открыта без {self.model.missing_count} компонентов ({len(m)} видов): их файлов нет "
                f"в папке сборки — обычно это стандартные и покупные изделия с сервера.\n\n" +
                "\n".join(lines) + more)

    # ------------------------------------------------------------- роли
    def _reclassify(self):
        self.cls = Classifier(self.model, self.scenario.fixture_main, self.scenario.roles, self.scenario.split)
        self._fill_tree()
        self._fill_table()
        if getattr(self, "picker", None) is not None and self.picker.isVisible():
            self.picker.rebuild()

    def _fill_tree(self):
        self._updating = True
        self.tree.clear()
        main = self.scenario.fixture_main

        icons = {}

        def role_icon(role):
            """Квадратик цвета роли: деталь — красный, оснастка — серый, прижим — зелёный."""
            if role not in icons:
                pm = QPixmap(12, 12)
                pm.fill(QColor(ROLE_COLORS[role]))
                icons[role] = QIcon(pm)
            return icons[role]

        def add(node: Node, parent_item):
            item = QTreeWidgetItem([node.label, ""])
            item.setData(0, KEY_ROLE, node.key)
            tips = []
            if node.is_group:
                f = QFont()
                f.setItalic(True)
                item.setFont(0, f)
                tips.append("Группа — правый клик: переименовать, разгруппировать")
            for title, idx in (("Цвет в сборке NX", node.color), ("Цвет в файле детали", node.part_color)):
                rgb = self.model.palette.get(idx) if idx is not None else None
                if rgb and (title.startswith("Цвет в сборке") or node.part_color != node.color):
                    hexc = QColor.fromRgbF(*rgb).name()   # только в подсказке — не путать с цветом роли
                    tips.append(f"{title}: <span style='color:{hexc}'>■■■</span> (№{idx})")
            role = self.cls.role(node)
            if role:
                item.setIcon(0, role_icon(role))
                item.setText(1, ROLE_TITLES[role] + (" *" if node.key in self.scenario.roles else "") +
                             (" — заменён" if node.replaced_by else "") +
                             (" — разобран" if node.key in self.cls.split and not node.replaced_by else ""))
                item.setForeground(1, QBrush(QColor(ROLE_COLORS[role]).darker(130)))
            if node.replaced_by:
                tips.append(f"Заменён деталью из файла:<br>{node.replaced_by}")
            if tips:
                item.setToolTip(0, "<br>".join(tips))
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
        if self.model.kind == "bodies":
            n_bodies = sum(1 for n in self.model.root.walk() if n.mesh)
            self.fixture_label.setText(
                f"Деталь из тел («шоп»): <b>{n_bodies}</b> тел. По умолчанию всё — оснастка. Выделите тела "
                f"детали или прижима → правый клик → «Объединить в группу» и задайте группе роль.")
        else:
            self.fixture_label.setText(
                f"Оснастка: <b>{main_node.designation}</b>, префикс <b>{self.cls.prefix}</b>" if main_node
                else "<span style='color:#b00'>Основная деталь оснастки не выбрана</span>")
        if self.model.missing:
            self.missing_label.setText(f"Без {self.model.missing_count} незагруженных компонентов "
                                       f"(стандартные и покупные изделия) — подробнее в подсказке")
            self.missing_label.setToolTip(self._missing_text(40))
            self.missing_label.show()
        else:
            self.missing_label.hide()
        self._updating = False

    def _selected_keys(self) -> list[str]:
        return [it.data(0, KEY_ROLE) for it in self.tree.selectedItems()]

    def _tree_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item or not self.model:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        keys = [k for k in self._selected_keys() if k in self.model.by_key]
        nodes = [self.model.by_key[k] for k in keys]
        movable_nodes = [n for n in nodes if n.parent is not None]
        menu = QMenu(self)

        if len(nodes) == 1 and self.model.kind != "bodies" and not nodes[0].is_group:
            node = nodes[0]
            a = menu.addAction("Основная деталь оснастки (префикс %s)" % prefix_of(node.designation))
            a.triggered.connect(lambda: self._set_fixture_main(node.key))
            menu.addSeparator()

        if len(nodes) == 1 and nodes[0].parent is not None and not nodes[0].is_group and nodes[0].children:
            node = nodes[0]
            if node.key in self.scenario.split:
                menu.addAction("Собрать обратно в одну строку").triggered.connect(lambda: self._join(node))
            else:
                n_parts = sum(1 for c in node.children if any(x.mesh for x in c.walk()))
                menu.addAction(f"Разобрать на составляющие ({n_parts} шт.) — закладывать по очереди"
                               ).triggered.connect(lambda: self._split(node))
            menu.addSeparator()

        if len(nodes) == 1 and nodes[0].part_color is not None and nodes[0].parent is not None:
            body = nodes[0]
            same = [c for c in body.parent.children if c.part_color == body.part_color]
            if len(same) > 1:
                menu.addAction(f"Выделить тела того же цвета ({len(same)} шт.)").triggered.connect(
                    lambda: self.select_in_tree([c.key for c in same]))
                menu.addSeparator()

        if (len(nodes) == 1 and nodes[0].parent is not None and not nodes[0].is_group and not nodes[0].is_body
                and self.model.kind == "assembly"):
            node = nodes[0]
            if node.key in self.scenario.replacements:
                menu.addAction("Вернуть исходную деталь (убрать замену)").triggered.connect(
                    lambda: self._replace_revert(node.key))
            elif self.model.original_leaves(node.key) or node.mesh:
                menu.addAction("Заменить деталью из файла… (узел с деревом деталей)").triggered.connect(
                    lambda: self._replace_start(node))
                menu.addAction("Заменить деталью из открытого NX…").triggered.connect(
                    lambda: self._replace_from_nx(node))
            menu.addSeparator()

        groupable = [n for n in movable_nodes if not n.is_group]
        if groupable and len({id(n.parent) for n in groupable}) == 1:
            a = menu.addAction(f"Объединить в группу… ({len(groupable)} шт.)")
            a.triggered.connect(lambda: self._group(groupable))
        groups = [n for n in nodes if n.is_group]
        if len(groups) == 1:
            menu.addAction("Переименовать группу…").triggered.connect(lambda: self._rename_group(groups[0]))
        if groups:
            menu.addAction("Разгруппировать").triggered.connect(lambda: self._ungroup(groups))
        if not menu.isEmpty():
            menu.addSeparator()

        if movable_nodes:
            mkeys = [n.key for n in movable_nodes]
            suffix = f" ({len(mkeys)} шт.)" if len(mkeys) > 1 else ""
            for role, title in ROLE_TITLES.items():
                a = menu.addAction("Роль: " + title + suffix)
                a.setCheckable(True)
                a.setChecked(all(self.scenario.roles.get(k) == role for k in mkeys))
                a.triggered.connect(lambda _=False, r=role: self._set_role(mkeys, r))
            a = menu.addAction("Роль: по умолчанию" + suffix)
            a.setEnabled(any(k in self.scenario.roles for k in mkeys))
            a.triggered.connect(lambda: self._set_role(mkeys, None))
            menu.addSeparator()
        menu.addAction("Приблизить в 3D").triggered.connect(self.focus_selection)
        theme.popup(menu, self.tree.viewport().mapToGlobal(pos))

    def _set_fixture_main(self, key):
        self.scenario.fixture_main = key
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    def _set_role(self, keys, role):
        for key in ([keys] if isinstance(keys, str) else keys):
            if role is None:
                self.scenario.roles.pop(key, None)
            else:
                self.scenario.roles[key] = role
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    # ------------------------------------------------------------- группы тел
    def _ask_group_name(self, title, default) -> str | None:
        while True:
            name, ok = QInputDialog.getText(self, APP_TITLE, title, text=default)
            if not ok:
                return None
            name = name.strip()
            if not name or "/" in name:
                QMessageBox.warning(self, APP_TITLE, "Имя группы не должно быть пустым и содержать «/».")
            elif name in self.scenario.groups and name != default:
                QMessageBox.warning(self, APP_TITLE, f"Группа «{name}» уже есть.")
            else:
                return name

    def _group(self, nodes: list[Node]):
        n = len(self.scenario.groups) + 1
        while f"Группа {n}" in self.scenario.groups:
            n += 1
        name = self._ask_group_name(f"Имя группы ({len(nodes)} шт.). Слово «прижим» или «палец» в имени — "
                                    f"группа сразу станет прижимом:", f"Группа {n}")
        if not name:
            return
        keys = [x.key for x in nodes]
        gkey = nodes[0].parent.key + "/" + GROUP_PREFIX + name
        roles = [self.scenario.roles.pop(k) for k in keys if k in self.scenario.roles]
        if roles:  # роль, назначенная членам вручную, переходит к группе
            self.scenario.roles[gkey] = max(set(roles), key=roles.count)
        self._merge_refs(keys, gkey)
        self.scenario.groups[name] = keys
        self._regroup()

    def _rename_group(self, group: Node):
        old = group.name
        new = self._ask_group_name("Новое имя группы:", old)
        if not new or new == old:
            return
        old_key = group.key
        new_key = group.parent.key + "/" + GROUP_PREFIX + new
        self.scenario.groups = {(new if k == old else k): v for k, v in self.scenario.groups.items()}
        self._replace_key(old_key, new_key)
        self._regroup()

    def _ungroup(self, groups: list[Node]):
        for g in groups:
            members = self.scenario.groups.pop(g.name, [])
            role = self.scenario.roles.get(g.key)
            if role:  # роль группы остаётся у её членов
                for k in members:
                    self.scenario.roles[k] = role
            self._expand_refs(g.key, members)
            self._replace_key(g.key, None)
        self._regroup()

    def _merge_refs(self, keys, new_key):
        """Отметки этапов у keys заменить одной отметкой new_key (для «добавить» — на самом раннем этапе)."""
        keys = set(keys)
        first_add = next((i for i, st in enumerate(self.scenario.steps) if keys & set(st.add)), None)
        for i, st in enumerate(self.scenario.steps):
            for lst, keep in ((st.add, i == first_add), (st.show, None), (st.hide, None)):
                hit = keys & set(lst)
                if not hit:
                    continue
                lst[:] = [k for k in lst if k not in keys]
                if (keep if keep is not None else True) and new_key not in lst:
                    lst.append(new_key)

    def _expand_refs(self, key, new_keys):
        """Отметку key заменить отметками new_keys."""
        for st in self.scenario.steps:
            for lst in (st.add, st.show, st.hide):
                if key in lst:
                    lst.remove(key)
                    lst.extend(k for k in new_keys if k not in lst)

    # ------------------------------------------------------------- разбор узла на составляющие
    def _split(self, node: Node):
        parts = [c.key for c in node.children if any(x.mesh for x in c.walk())]
        placed = self.scenario.step_of_part(node.key)
        # закладку узла не размножаем на все детали — их распределяют по этапам заново
        for st in self.scenario.steps:
            if node.key in st.add:
                st.add.remove(node.key)
        self.scenario.split.append(node.key)
        self._expand_refs(node.key, parts)   # видимость (прижимы / скрытие) переносится на составляющие
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()
        msg = f"«{node.short_label}» разобран: {len(parts)} составляющих — отдельные строки таблицы этапов."
        if placed is not None:
            msg += (f"\n\nУзел был отмечен на этапе {placed + 1}. Теперь распределите его детали по этапам: "
                    f"на нужном этапе щёлкните по клетке детали.")
        msg += ("\n\nЛишнее (например, сварные швы) удобно скрыть: правый клик по такому телу → «Выделить тела "
                "того же цвета» → правый клик → «Роль: Скрыть».")
        QMessageBox.information(self, APP_TITLE, msg)

    def _join(self, node: Node):
        self.scenario.split = [k for k in self.scenario.split if k != node.key]
        inner = [n.key for n in node.walk() if n is not node]
        self.scenario.split = [k for k in self.scenario.split if k not in inner]
        self._merge_refs(inner, node.key)
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    def _replace_key(self, old, new):
        """Переименовать (new) или удалить (None) ключ во всех ссылках сценария."""
        sc = self.scenario
        if old in sc.roles:
            role = sc.roles.pop(old)
            if new:
                sc.roles[new] = role
        if sc.fixture_main == old:
            sc.fixture_main = new
        for st in sc.steps:
            for lst in (st.add, st.show, st.hide):
                if old in lst:
                    i = lst.index(old)
                    if new:
                        lst[i] = new
                    else:
                        del lst[i]

    # ------------------------------------------------------------- замена узла деталью из файла
    def _replace_start(self, node: Node):
        start_dir = os.path.dirname(self.prt_path or "")
        path, _ = QFileDialog.getOpenFileName(
            self, f"Чем заменить «{node.short_label}» — деталь или сборка NX", start_dir, "Файлы NX (*.prt)")
        if not path:
            return
        path = os.path.abspath(path)
        if nx_bridge.is_export_fresh(path):
            self._replace_finish(node.key, path)
        else:
            self.run_nx_export(path=path, on_done=lambda: self._replace_finish(node.key, path))

    def _replace_finish(self, key: str, path: str):
        node = self.model.by_key[key]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            repl = replace.load_replacement(path)
            res = replace.align(self.model, key, repl)
        finally:
            QApplication.restoreOverrideCursor()
        name = nx_link.model_title(path)
        if res.matched == 0:
            QMessageBox.critical(self, APP_TITLE,
                                 f"Не удалось совместить «{name}» с «{node.short_label}»: ни одна деталь файла не "
                                 f"совпала по форме с телами узла. Проверьте, что выбран файл того же узла.")
            return
        if not res.ok and QMessageBox.question(
                self, APP_TITLE, f"Совместить удалось частично: {res.summary}.\n\nНе совпавшие детали могут встать "
                                 f"неточно. Всё равно заменить?") != QMessageBox.Yes:
            return
        self._drop_refs([n.key for n in node.walk() if n is not node])
        for st in self.scenario.steps:   # закладку узла — заново по деталям
            if key in st.add:
                st.add.remove(key)
        self.scenario.replacements[key] = {"file": path, "matrix": [round(float(v), 6) for v in res.matrix.flatten()],
                                           "summary": res.summary}
        if key not in self.scenario.split:
            self.scenario.split.append(key)
        self.model.set_replacement(key, repl, res.matrix)
        self.model.apply_groups(self.scenario.groups)
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()
        QMessageBox.information(self, APP_TITLE,
                                f"«{node.short_label}» заменён деталью «{name}»: {res.summary}.\n\n"
                                f"Детали узла теперь отдельные строки таблицы этапов — распределите их по этапам. "
                                f"Исходные файлы NX не изменены; вернуть — правый клик по узлу.")

    def _replace_revert(self, key: str):
        node = self.model.by_key[key]
        self._drop_refs([n.key for n in node.walk() if n is not node])
        self.scenario.replacements.pop(key, None)
        self.model.clear_replacement(key)
        self.model.apply_groups(self.scenario.groups)
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    def _drop_refs(self, keys):
        """Убрать из сценария все ссылки на узлы keys (роли, этапы, группы)."""
        keys = set(keys)
        sc = self.scenario
        for k in keys:
            sc.roles.pop(k, None)
        sc.split = [k for k in sc.split if k not in keys]
        for st in sc.steps:
            for lst in (st.add, st.show, st.hide):
                lst[:] = [k for k in lst if k not in keys]
        for name in list(sc.groups):
            sc.groups[name] = [k for k in sc.groups[name] if k not in keys]
            if not sc.groups[name]:
                del sc.groups[name]

    def _regroup(self):
        self.model.apply_groups(self.scenario.groups)
        self.mark_dirty()
        self._reclassify()
        self.refresh_view()

    # ------------------------------------------------------------- выбор в дереве → 3D
    def _apply_selection(self):
        if not self.scene or not self.model:
            return
        nodes = [self.model.by_key[k] for k in self._selected_keys() if k in self.model.by_key]
        nodes = [n for n in nodes if n.parent is not None]   # корень = вся модель — не подсвечиваем
        self.scene.highlight(self.plotter, nodes)
        if getattr(self, "picker", None) is not None and self.picker.isVisible():
            self.picker.show_selection([n.key for n in nodes])
        if nodes:
            self.statusBar().showMessage(f"Выбрано: {len(nodes)}", 3000)

    def _row_header_clicked(self, row):
        """Щелчок по названию строки таблицы — выбрать этот элемент в дереве и подсветить в 3D."""
        if 0 <= row < len(self._rows) and self._rows[row][0] != "header":
            self.select_in_tree([self._rows[row][1].key])

    def select_in_tree(self, keys, reveal=False):
        """Выбрать узлы в дереве (подсветка в 3D идёт от выбора в дереве). reveal — показать строку таблицы."""
        if reveal:
            self._reveal_rows(keys)
        keys = set(keys)
        self.tree.clearSelection()
        first = None
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, KEY_ROLE) in keys:
                it.setSelected(True)
                first = first or it
            stack.extend(it.child(i) for i in range(it.childCount()))
        if first is not None:
            self.tree.scrollToItem(first)

    def _reveal_rows(self, keys):
        keys = set(keys)
        for r, (kind, obj) in enumerate(self._rows):
            if kind != "header" and obj.key in keys:
                self.table.scrollToItem(self.table.item(r, 0))
                self.table.verticalHeader().setHighlightSections(True)
                break

    # ------------------------------------------------------------- окно выбора по модели
    def open_picker(self):
        if not self.model:
            QMessageBox.information(self, APP_TITLE, "Сначала откройте сборку.")
            return
        if getattr(self, "picker", None) is None:
            from .picker import PickerWindow
            self.picker = PickerWindow(self)
            g = self.geometry()
            self.picker.move(g.x() + g.width() - self.picker.width() - 20, g.y() + 60)
        self.picker.setWindowTitle(f"Выбор по модели — {nx_link.model_title(self.prt_path)}")
        self.picker.show()
        self.picker.raise_()
        self.picker.rebuild(reset_camera=True)

    def apply_to_current_step(self, keys, on: bool):
        """Кнопки окна выбора: детали — заложить на текущем этапе / снять; прижимы — показать / скрыть."""
        cur = self.current_step
        if cur is None or not self.model:
            return
        parts = {n.key for n in self.cls.units(PART)}
        movables = {n.key for n in self.cls.units(MOVABLE)}
        step = self.scenario.steps[cur]
        done, skipped = 0, 0
        for k in keys:
            if k in parts:
                placed = self.scenario.step_of_part(k)
                if on and placed != cur:
                    for st in self.scenario.steps:
                        if k in st.add:
                            st.add.remove(k)
                    for st in self.scenario.steps[:cur + 1]:
                        if k in st.hide:
                            st.hide.remove(k)
                    step.add.append(k)
                    done += 1
                elif not on and placed == cur:
                    step.add.remove(k)
                    done += 1
            elif k in movables:
                if on and k not in step.show:
                    step.show.append(k)
                    done += 1
                elif not on and k in step.show:
                    step.show.remove(k)
                    done += 1
            else:
                skipped += 1
        if done:
            self.mark_dirty()
            self._sync_table()
            self.refresh_view()
        msg = f"Этап {cur + 1}: изменено {done}"
        if skipped:
            msg += f"; пропущено {skipped} — это не строки таблицы (оснастка или часть детали). " \
                   f"Назначьте роль или выберите «как в таблице»."
        self.statusBar().showMessage(msg, 6000)

    def focus_selection(self):
        nodes = [self.model.by_key[k] for k in self._selected_keys() if self.model and k in self.model.by_key]
        mesh = self.scene.highlight(self.plotter, nodes) if nodes and self.scene else None
        if mesh is None:
            return
        self.plotter.reset_camera(bounds=mesh.bounds)
        self.plotter.camera.zoom(0.6)
        self.plotter.render()

    def _cleanup_scenario(self):
        """Убирает из этапов ключи, которые больше не являются строками таблицы."""
        if not self.cls:
            return
        parts = {n.key for n in self.cls.units(PART)}
        movables = {n.key for n in self.cls.units(MOVABLE)}
        for s in self.scenario.steps:
            s.add = [k for k in s.add if k in parts]
            s.show = [k for k in s.show if k in movables]
        # скрывать можно только детали, заложенные на более ранних этапах
        for i, s in enumerate(self.scenario.steps):
            earlier = {k for st in self.scenario.steps[:i] for k in st.add}
            s.hide = [k for k in s.hide if k in earlier]

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
        for r, (kind, obj) in enumerate(self._rows):
            if kind == "header":
                t.verticalHeaderItem(r).setFont(bold)
            else:
                rgb = self.model.rgb(obj)
                if rgb:  # цвет тела из NX — чтобы различать Body12, Body13…
                    pm = QPixmap(10, 10)
                    pm.fill(QColor.fromRgbF(*rgb))
                    t.verticalHeaderItem(r).setIcon(QIcon(pm))
                t.verticalHeaderItem(r).setToolTip("Щёлкните, чтобы подсветить в 3D")
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
                bg, tip, checked = self._cell_look(kind, obj.key, part_step, c, st, c == cur)
                it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
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

    @staticmethod
    def _cell_look(kind, key, part_step, c, st, current):
        """Цвет, подсказка и галочка ячейки (строка детали/прижима, столбец этапа c)."""
        other_tip = f"Щёлкните, чтобы перейти к этапу {c + 1}"
        if kind == MOVABLE:
            on = key in st.show
            if current:
                return (theme.CELL_MOV_ON if on else theme.CELL_CURRENT,
                        "Щёлкните, чтобы скрыть прижим на этом этапе" if on else "Щёлкните, чтобы показать прижим", on)
            return (theme.CELL_MOV_ON_OTHER if on else theme.CELL_OTHER), other_tip, on
        if part_step is None:  # деталь ещё никуда не заложена
            return (theme.CELL_CURRENT if current else theme.CELL_OTHER,
                    "Щёлкните, чтобы заложить деталь на этом этапе" if current else other_tip, False)
        if c == part_step:  # этап закладки
            return (theme.CELL_PART_ON if current else theme.CELL_PART_ON_OTHER,
                    "Деталь закладывается здесь. Щёлкните, чтобы отменить" if current else other_tip, True)
        if c < part_step:  # деталь ещё не заложена
            return (theme.CELL_LOCKED if current else theme.CELL_OTHER,
                    f"Деталь закладывается на этапе {part_step + 1}" if current else other_tip, False)
        visible = key not in st.hide  # ранее заложенная деталь
        if current:
            tip = (f"Заложена на этапе {part_step + 1}. Щёлкните, чтобы " +
                   ("скрыть на этом этапе" if visible else "снова показать"))
            return (theme.CELL_PART_SHOWN if visible else theme.CELL_CURRENT), tip, visible
        return (theme.CELL_PART_SHOWN_OTHER if visible else theme.CELL_OTHER), other_tip, visible

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
        theme.popup(menu, self.table.horizontalHeader().mapToGlobal(pos))

    def _header_clicked(self, col):
        if col != self.current_step:
            self.select_step(col)
            self._switched_at = time.monotonic()

    def _cell_menu(self, pos):
        """Правый клик по клетке детали в текущем этапе: перенести закладку на этот этап."""
        item = self.table.itemAt(pos)
        if item is None or item.data(KEY_KIND) != PART:
            return
        col, key = item.column(), item.data(KEY_ROLE)
        if col != self.current_step:
            self.select_step(col)
            return
        placed = self.scenario.step_of_part(key)
        menu = QMenu(self)
        if placed == col:
            menu.addAction("Не закладывать на этом этапе").triggered.connect(lambda: self._place_part(key, None))
        else:
            text = f"Закладывать на этапе {col + 1}"
            if placed is not None:
                text += f" (перенести с этапа {placed + 1})"
            menu.addAction(text).triggered.connect(lambda: self._place_part(key, col))
        menu.addAction("Подсветить в 3D").triggered.connect(lambda: self.select_in_tree([key]))
        theme.popup(menu, self.table.viewport().mapToGlobal(pos))

    def _place_part(self, key, col):
        """Закладка детали на этапе col (None — снять закладку)."""
        for i, st in enumerate(self.scenario.steps):
            if key in st.add:
                st.add.remove(key)
            if col is not None and i <= col and key in st.hide:
                st.hide.remove(key)   # до закладки и на ней деталь не может быть «убрана»
        if col is not None:
            self.scenario.steps[col].add.append(key)
        self.mark_dirty()
        self._sync_table()
        self.refresh_view()

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
            placed = self.scenario.step_of_part(key)
            if placed is not None and placed > col:
                self.statusBar().showMessage(f"Деталь закладывается на этапе {placed + 1}. "
                                             f"Чтобы перенести — перейдите на него и снимите отметку.", 5000)
                return
            # заложена раньше — галочка управляет видимостью на этом этапе
            lst = step.hide if placed is not None and placed < col else step.add
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
            self._finish_detail_edit(True)
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
        self._update_details_bar()
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
        steps.insert(at, Step(show=list(prev.show) if prev else [], hide=list(prev.hide) if prev else [],
                              direction=prev.direction if prev else "-Z"))
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
        if self._editing_detail is not None:
            return   # в 3D сейчас вид уточнения
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

    # ------------------------------------------------------------- уточнения на листе этапа
    def _cur_step_obj(self) -> Step | None:
        if not self.model or self.current_step is None or self.show_all.isChecked():
            return None
        return self.scenario.steps[self.current_step] if self.current_step < len(self.scenario.steps) else None

    def _update_details_bar(self):
        while self.details_box.count():
            w = self.details_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        step = self._cur_step_obj()
        editing = self._editing_detail is not None
        for j, d in enumerate(step.details if step else []):
            text = lay.detail_label(j, d)
            b = QPushButton(text if len(text) <= 24 else text[:23] + "…")
            b.setToolTip(text + " — щёлкните: изменить вид, подпись, удалить")
            b.setCursor(Qt.PointingHandCursor)
            b.setEnabled(not editing)
            menu = QMenu(b)
            menu.addAction("Изменить вид в 3D").triggered.connect(lambda _=False, j=j: self._start_detail_edit(j))
            menu.addAction("Подпись…").triggered.connect(lambda _=False, j=j: self._rename_detail(j))
            a = menu.addAction("Обводить место на общем виде")
            a.setCheckable(True)
            a.setChecked(d.mark)
            a.toggled.connect(lambda on, j=j: self._set_detail_mark(j, on))
            menu.addAction("Компоновка листа…").triggered.connect(lambda _=False, j=j: self.open_layout(j))
            menu.addSeparator()
            menu.addAction("Удалить").triggered.connect(lambda _=False, j=j: self._delete_detail(j))
            b.setMenu(menu)
            self.details_box.addWidget(b)
        if step and not step.details:
            self.details_box.addWidget(label("нет — на листе только общий вид", "hint"))
        self.btn_add_detail.setEnabled(step is not None and not editing)
        self.btn_layout.setEnabled(step is not None and not editing)
        for w in (self.detail_edit_label, self.btn_detail_ok, self.btn_detail_cancel):
            w.setVisible(editing)

    def add_detail(self):
        """Новое уточнение текущего этапа: крупный вид выделенного элемента или вид, настроенный вручную."""
        step = self._cur_step_obj()
        if step is None or self._editing_detail is not None:
            self.statusBar().showMessage("Сначала выберите этап (снимите «Показать всю сборку»)", 5000)
            return
        self._remember_view()
        nodes = [self.model.by_key[k] for k in self._selected_keys() if k in self.model.by_key]
        cam = self.scene.zoom_camera(self.plotter, nodes) if nodes else None
        d = Detail(camera=cam or SceneBuilder.capture_camera(self.plotter))
        w, h = self.plotter.window_size
        mark = lay.visible_mark(SceneBuilder._project(self.plotter, d.camera, (h, w))) if cam else None
        d.rect = lay.free_rect([o.rect for o in step.details], mark)
        step.details.append(d)
        self.mark_dirty()
        self._start_detail_edit(len(step.details) - 1, new=True, zoomed=bool(cam))

    def _start_detail_edit(self, j, new=False, zoomed=True):
        step = self._cur_step_obj()
        if step is None or j >= len(step.details):
            return
        self._finish_detail_edit(True)
        self._remember_view()
        self._detail_back = SceneBuilder.capture_camera(self.plotter)
        self._editing_detail = (self.current_step, j, new)
        SceneBuilder.apply_camera(self.plotter, step.details[j].camera, self.scene.model_bounds())
        self.plotter.render()
        how = "проверьте вид" if zoomed else "приблизьте нужное место (колесо мыши), поверните"
        self.detail_edit_label.setText(f"Вид {detail_letter(j)}: {how} в 3D и нажмите «Готово»")
        self._update_details_bar()

    def _finish_detail_edit(self, save: bool, layout: bool = False):
        if self._editing_detail is None:
            return
        si, j, new = self._editing_detail
        self._editing_detail = None
        step = self.scenario.steps[si] if si < len(self.scenario.steps) else None
        if step is not None and j < len(step.details):
            if save:
                cam = SceneBuilder.capture_camera(self.plotter)
                if cam != step.details[j].camera:   # новый вид — обводка снова по нему
                    step.details[j].mark_adj = [0.0, 0.0, 1.0]
                step.details[j].camera = cam
            elif new:
                del step.details[j]
            self.mark_dirty()
        if self._detail_back and self.scene:
            SceneBuilder.apply_camera(self.plotter, self._detail_back, self.scene.model_bounds())
            self.plotter.render()
        self._update_details_bar()
        if layout and save and new and step is not None and si == self.current_step:
            self.open_layout(j)

    def _rename_detail(self, j):
        step = self._cur_step_obj()
        if step is None or j >= len(step.details):
            return
        d = step.details[j]
        text, ok = QInputDialog.getText(self, APP_TITLE, f"Вид {detail_letter(j)}: подпись (можно пустую)", text=d.title)
        if ok:
            d.title = text.strip()
            self.mark_dirty()
            self._update_details_bar()

    def _set_detail_mark(self, j, on):
        step = self._cur_step_obj()
        if step is not None and j < len(step.details):
            step.details[j].mark = on
            self.mark_dirty()

    def _delete_detail(self, j):
        step = self._cur_step_obj()
        if step is not None and j < len(step.details):
            del step.details[j]
            self.mark_dirty()
            self._update_details_bar()

    def open_layout(self, j=0):
        step = self._cur_step_obj()
        if step is None:
            return
        self._remember_view()
        from .layout import LayoutDialog
        dlg = LayoutDialog(self, self.scenario, self.cls, self.scene, self.current_step, j)
        if (getattr(dlg, "exec", None) or dlg.exec_)():
            step.details = dlg.result_details
            step.arrows = dlg.result_arrows
            self.mark_dirty()
            self._update_details_bar()
            self.refresh_view()

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
        if self.tree.selectedItems():
            self._apply_selection()
        if reset and step is not None:
            self._entry_camera = SceneBuilder.capture_camera(self.plotter)
            self._update_camera_mark()

    # ------------------------------------------------------------- PDF
    def export_pdf(self):
        if not self.model:
            return
        base = (os.path.splitext(self.scenario_path)[0].replace(paths.SCENARIO_EXT[:-5], "")
                if self.scenario_path else os.path.join(paths.scenarios_dir(), nx_link.model_stem(self.prt_path)))
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт PDF", base + ".pdf", "PDF (*.pdf)")
        if not path:
            return
        self._finish_detail_edit(True)
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
            export_pdf(path, self.scenario, self.cls, self.scene, title=nx_link.model_title(self.prt_path),
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
    nx_link.register_app()
    w = MainWindow()
    w.show()
    w._watch_inbox()
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.lower().endswith((".yaml", ".yml")):
            w._load_scenario_file(arg)
        elif arg.lower().endswith(".prt"):
            w._start(arg, Scenario(model=arg))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
