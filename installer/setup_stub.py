"""Установщик «Закладка в оснастку»: один exe — эта программа + архив программы, дописанный в конец файла.

  Osnastka-Setup-<версия>.exe                 — окно установки (права администратора запрашиваются при запуске)
  Osnastka-Setup-<версия>.exe /S [/D=путь] [/NODESKTOP] [/NOSTARTMENU] — без окон, для администраторов
  <папка программы>\\uninstall.exe            — удаление (копия установщика без архива; так же /S)

Ставит программу для всех пользователей (по умолчанию в Program Files), ярлыки в меню «Пуск» и на рабочем
столе, запись в «Программы и компоненты». Данные пользователей (сценарии в «Документах», обновления и кэш
в %LOCALAPPDATA%\\Osnastka) не трогает. Собирается tools/make_installer.py (Python 3.8 — работает и на Windows 7).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

TRAILER = b"OSNPAYLD"          # + 8 байт: длина архива (little-endian)
APP_EXE = "Osnastka.exe"
TITLE = "Закладка в оснастку"
SHORTCUT = TITLE + ".lnk"
MANIFEST = "install.json"
UNINSTALL_EXE = "uninstall.exe"
REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Osnastka"
NO_WINDOW = 0x08000000


class InstallError(Exception):
    pass


# ------------------------------------------------------------------ архив внутри exe
class _Slice(io.RawIOBase):
    """Часть файла [start, start + length) как отдельный файл — для zipfile."""

    def __init__(self, path, start, length):
        super().__init__()
        self._f = open(path, "rb")
        self._start, self._len, self._pos = start, length, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, off, whence=0):
        base = {0: 0, 1: self._pos, 2: self._len}[whence]
        self._pos = max(0, base + off)
        return self._pos

    def tell(self):
        return self._pos

    def readinto(self, b):
        n = min(len(b), self._len - self._pos)
        if n <= 0:
            return 0
        self._f.seek(self._start + self._pos)
        data = self._f.read(n)
        b[:len(data)] = data
        self._pos += len(data)
        return len(data)

    def close(self):
        self._f.close()
        super().close()


def payload_info(exe=None):
    """(смещение, длина) архива программы в exe или None (это uninstall.exe)."""
    exe = exe or sys.executable
    size = os.path.getsize(exe)
    with open(exe, "rb") as f:
        f.seek(size - 16)
        tail = f.read(16)
    if tail[:8] != TRAILER:
        return None
    length = int.from_bytes(tail[8:], "little")
    return size - 16 - length, length


def open_payload() -> zipfile.ZipFile:
    info = payload_info()
    if not info:
        raise InstallError("В установщике нет архива программы — файл повреждён или это uninstall.exe.")
    return zipfile.ZipFile(io.BufferedReader(_Slice(sys.executable, *info), 1 << 20))


def read_setup_info(z: zipfile.ZipFile) -> dict:
    return json.loads(z.read("setup.json").decode("utf-8"))


# ------------------------------------------------------------------ система
def is_win7_or_older() -> bool:
    v = sys.getwindowsversion()
    return (v.major, v.minor) < (6, 2)


def program_files() -> str:
    return os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles") or r"C:\Program Files"


def start_menu_dir() -> str:
    return os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows", "Start Menu",
                        "Programs")


def desktop_dir() -> str:
    return os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop")


def user_start_menu_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs")


def user_desktop_dir() -> str:
    return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")


def _roots():
    import winreg
    return winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER


def registered_location() -> str | None:
    import winreg
    for root in _roots():
        try:
            with winreg.OpenKey(root, REG_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                loc = winreg.QueryValueEx(k, "InstallLocation")[0]
                if loc:
                    return loc
        except OSError:
            pass
    return None


def default_target() -> str:
    return registered_location() or os.path.join(program_files(), "Osnastka")


def exe_in_use(path: str) -> bool:
    """Запущенный exe нельзя открыть на запись."""
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def make_shortcut(lnk: str, target: str):
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:OSN_LNK);"
              "$s.TargetPath=$env:OSN_TARGET;$s.WorkingDirectory=$env:OSN_DIR;"
              "$s.IconLocation=$env:OSN_TARGET+',0';$s.Description=$env:OSN_DESC;$s.Save()")
    env = dict(os.environ, OSN_LNK=lnk, OSN_TARGET=target, OSN_DIR=os.path.dirname(target), OSN_DESC=TITLE)
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                        script], env=env, creationflags=NO_WINDOW, capture_output=True)
    if r.returncode != 0 or not os.path.exists(lnk):
        raise InstallError("Не удалось создать ярлык " + lnk)


def register(target: str, info: dict, size_bytes: int):
    """Запись в «Программы и компоненты»: для всех пользователей, без прав администратора — для текущего."""
    import winreg
    try:
        key = winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, REG_KEY, 0, winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY)
    except PermissionError:
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_WRITE)
    with key as k:
        uninst = os.path.join(target, UNINSTALL_EXE)
        for name, value in (("DisplayName", f"{TITLE} (Osnastka)"),
                            ("DisplayVersion", info.get("version", "")),
                            ("Publisher", "Osnastka"),
                            ("InstallLocation", target),
                            ("DisplayIcon", os.path.join(target, APP_EXE) + ",0"),
                            ("UninstallString", f'"{uninst}"'),
                            ("QuietUninstallString", f'"{uninst}" /S')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_bytes // 1024)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def unregister(target: str):
    """Убрать запись, если она про эту папку."""
    import winreg
    for root in _roots():
        try:
            with winreg.OpenKey(root, REG_KEY, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                loc = winreg.QueryValueEx(k, "InstallLocation")[0]
            if os.path.normcase(os.path.normpath(loc)) == os.path.normcase(os.path.normpath(target)):
                winreg.DeleteKeyEx(root, REG_KEY, winreg.KEY_WOW64_64KEY, 0)
        except OSError:
            pass


# ------------------------------------------------------------------ установка и удаление
def check_target(target: str):
    """Папка установки: пустая, несуществующая или с прежней установкой программы."""
    if not os.path.isabs(target) or len(os.path.splitdrive(os.path.normpath(target))[1]) <= 1:
        raise InstallError("Укажите полный путь к папке, не корень диска.")
    if os.path.isdir(target) and os.listdir(target):
        ours = os.path.exists(os.path.join(target, MANIFEST)) or os.path.exists(os.path.join(target, APP_EXE))
        if not ours:
            raise InstallError(f"Папка не пустая и это не папка программы:\n{target}\n\nВыберите пустую папку.")
    if exe_in_use(os.path.join(target, APP_EXE)):
        raise InstallError("Программа запущена. Закройте «Закладку в оснастку» (у всех пользователей этого "
                           "компьютера) и повторите.")


def remove_installed(target: str):
    """Удалить прежнюю установку из папки: по списку файлов, иначе — всю папку программы."""
    manifest = os.path.join(target, MANIFEST)
    files = None
    if os.path.exists(manifest):
        try:
            with open(manifest, encoding="utf-8") as f:
                files = json.load(f).get("files")
        except (OSError, ValueError):
            files = None
    if files is None:
        if os.path.isdir(os.path.join(target, "_internal")) or os.path.exists(os.path.join(target, APP_EXE)):
            shutil.rmtree(target, ignore_errors=True)
        return
    for rel in files + [MANIFEST, UNINSTALL_EXE]:
        p = os.path.join(target, rel)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    for root, dirs, _ in os.walk(target, topdown=False):   # пустые папки
        for d in dirs:
            try:
                os.rmdir(os.path.join(root, d))
            except OSError:
                pass


def install(target: str, desktop: bool = True, start_menu: bool = True, progress=None) -> dict:
    """progress(доля 0..1, текст)."""
    progress = progress or (lambda *_: None)
    target = os.path.normpath(target)
    with open_payload() as z:
        info = read_setup_info(z)
        if info.get("flavor") == "win10" and is_win7_or_older():
            raise InstallError("Этот установщик — для Windows 10/11. Для Windows 7 нужен Osnastka-win7-Setup.")
        check_target(target)
        progress(0.0, "Удаление прежней версии…")
        remove_installed(target)
        os.makedirs(target, exist_ok=True)
        items = [i for i in z.infolist() if i.filename.startswith("app/") and not i.is_dir()]
        total = sum(i.file_size for i in items) or 1
        done = 0
        files = []
        for i in items:
            rel = i.filename[4:]
            dst = os.path.join(target, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(i) as src, open(dst, "wb") as out:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    progress(0.95 * done / total, "Копирование файлов… " + rel)
            files.append(rel.replace("/", "\\"))
    progress(0.96, "Ярлыки и регистрация…")
    off, _ = payload_info()
    with open(sys.executable, "rb") as src, open(os.path.join(target, UNINSTALL_EXE), "wb") as out:
        left = off
        while left > 0:
            chunk = src.read(min(left, 1 << 20))
            if not chunk:
                break
            out.write(chunk)
            left -= len(chunk)
    with open(os.path.join(target, MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"version": info.get("version"), "flavor": info.get("flavor"), "files": files,
                   "shortcuts": []}, f, ensure_ascii=False, indent=1)
    exe = os.path.join(target, APP_EXE)
    shortcuts, warnings = [], []
    for want, folders in ((start_menu, (start_menu_dir(), user_start_menu_dir())),
                          (desktop, (desktop_dir(), user_desktop_dir()))):
        if not want:
            continue
        err = ""
        for folder in folders:   # без прав администратора — ярлык только себе
            lnk = os.path.join(folder, SHORTCUT)
            try:
                make_shortcut(lnk, exe)
                shortcuts.append(lnk)
                break
            except InstallError as e:
                err = str(e)
        else:
            warnings.append(err)
    with open(os.path.join(target, MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"version": info.get("version"), "flavor": info.get("flavor"), "files": files,
                   "shortcuts": shortcuts}, f, ensure_ascii=False, indent=1)
    register(target, info, total)
    progress(1.0, "Готово")
    return dict(info, warnings=warnings, target=target)


def uninstall(target: str):
    if exe_in_use(os.path.join(target, APP_EXE)):
        raise InstallError("Программа запущена. Закройте её (у всех пользователей) и повторите удаление.")
    try:
        with open(os.path.join(target, MANIFEST), encoding="utf-8") as f:
            shortcuts = json.load(f).get("shortcuts") or []
    except (OSError, ValueError):
        shortcuts = []
    for lnk in shortcuts:
        try:
            os.remove(lnk)
        except OSError:
            pass
    remove_installed(target)
    try:
        os.rmdir(target)
    except OSError:
        pass
    unregister(target)


def relaunch_from_temp(args) -> bool:
    """uninstall.exe не может удалить сам себя — запускается его копия из TEMP."""
    here = os.path.dirname(os.path.abspath(sys.executable))
    tmp = os.path.join(tempfile.gettempdir(), f"osnastka_uninstall_{os.getpid()}.exe")
    shutil.copy2(sys.executable, tmp)
    subprocess.Popen([tmp, "--uninstall-from", here] + args, close_fds=True)
    return True


def schedule_self_delete():
    subprocess.Popen(f'cmd /c ping -n 4 127.0.0.1 >nul & del /f /q "{sys.executable}"',
                     creationflags=NO_WINDOW, close_fds=True)


# ------------------------------------------------------------------ окна
def message(text, error=False):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)


def ask(text) -> bool:
    import ctypes
    return ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x24) == 6


def run_gui():
    from PySide2.QtCore import Qt
    from PySide2.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                                   QProgressBar, QPushButton, QVBoxLayout)

    app = QApplication(sys.argv)
    try:
        with open_payload() as z:
            info = read_setup_info(z)
    except Exception as e:
        message(f"Установщик повреждён: {e}", error=True)
        return 1
    flavor = "Windows 7" if info.get("flavor") == "win7" else "Windows 10/11"

    dlg = QDialog()
    dlg.setWindowTitle(f"Установка — {TITLE} {info.get('version', '')}")
    dlg.setStyleSheet("""
        QDialog { background: #FFFFFF; }
        QLabel#title { font-size: 15pt; font-weight: 700; color: #1F2023; }
        QLabel#sub { color: #6B7078; }
        QLabel#bar { background: #D7262D; }
        QPushButton { padding: 6px 16px; border: 1px solid #DCDFE3; border-radius: 5px; background: #FFFFFF; }
        QPushButton:hover { border-color: #D7262D; color: #D7262D; }
        QPushButton#primary { background: #D7262D; color: #FFFFFF; border-color: #D7262D; font-weight: 600; }
        QPushButton#primary:disabled { background: #E9A9AC; border-color: #E9A9AC; }
        QLineEdit { padding: 5px; border: 1px solid #DCDFE3; border-radius: 5px; }
        QProgressBar { border: 1px solid #DCDFE3; border-radius: 5px; text-align: center; height: 18px; }
        QProgressBar::chunk { background: #D7262D; border-radius: 4px; }
    """)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(22, 18, 22, 18)
    v.setSpacing(10)
    title = QLabel(f"{TITLE}")
    title.setObjectName("title")
    v.addWidget(title)
    sub = QLabel(f"Версия {info.get('version', '')} · для {flavor}")
    sub.setObjectName("sub")
    v.addWidget(sub)
    bar = QLabel()
    bar.setObjectName("bar")
    bar.setFixedHeight(3)
    v.addWidget(bar)
    v.addWidget(QLabel("Папка установки:"))
    row = QHBoxLayout()
    path_edit = QLineEdit(default_target())
    row.addWidget(path_edit, 1)
    browse = QPushButton("Обзор…")
    row.addWidget(browse)
    v.addLayout(row)
    cb_desktop = QCheckBox("Ярлык на рабочем столе (для всех пользователей)")
    cb_desktop.setChecked(True)
    cb_menu = QCheckBox("Ярлык в меню «Пуск»")
    cb_menu.setChecked(True)
    v.addWidget(cb_desktop)
    v.addWidget(cb_menu)
    note = QLabel("Программа ставится для всех пользователей компьютера. Новые версии потом ставятся из самой "
                  "программы («Обновления») — без прав администратора. Сценарии и настройки пользователей "
                  "не затрагиваются.")
    note.setWordWrap(True)
    note.setObjectName("sub")
    v.addWidget(note)
    progress = QProgressBar()
    progress.setRange(0, 1000)
    progress.setValue(0)
    v.addWidget(progress)
    status = QLabel("")
    status.setObjectName("sub")
    v.addWidget(status)
    buttons = QHBoxLayout()
    buttons.addStretch()
    b_install = QPushButton("Установить")
    b_install.setObjectName("primary")
    b_close = QPushButton("Отмена")
    buttons.addWidget(b_install)
    buttons.addWidget(b_close)
    v.addLayout(buttons)
    dlg.resize(560, 0)

    def pick():
        d = QFileDialog.getExistingDirectory(dlg, "Папка установки", os.path.dirname(path_edit.text()) or "C:\\")
        if d:
            d = os.path.normpath(d)
            if os.path.basename(d).lower() != "osnastka":
                d = os.path.join(d, "Osnastka")
            path_edit.setText(d)

    last = [0.0]

    def on_progress(frac, text):
        progress.setValue(int(frac * 1000))
        now = time.monotonic()
        if now - last[0] > 0.05 or frac >= 1:
            last[0] = now
            status.setText(text if len(text) < 90 else text[:87] + "…")
            app.processEvents()

    state = {"done": False}

    def do_install():
        if state["done"]:
            dlg.accept()
            return
        for w in (b_install, b_close, browse, path_edit, cb_desktop, cb_menu):
            w.setEnabled(False)
        app.setOverrideCursor(Qt.WaitCursor)
        try:
            res = install(path_edit.text().strip(), cb_desktop.isChecked(), cb_menu.isChecked(), on_progress)
        except Exception as e:
            app.restoreOverrideCursor()
            for w in (b_install, b_close, browse, path_edit, cb_desktop, cb_menu):
                w.setEnabled(True)
            status.setText("")
            progress.setValue(0)
            message(str(e) if isinstance(e, InstallError) else f"Ошибка установки: {e}", error=True)
            return
        app.restoreOverrideCursor()
        state["done"] = True
        text = f"Установлено в {res['target']}.\nЗапускайте программу с ярлыка «{TITLE}»."
        if res["warnings"]:
            text += "\n\n" + "\n".join(res["warnings"])
        status.setText(text)
        b_install.setText("Готово")
        b_install.setEnabled(True)

    browse.clicked.connect(pick)
    b_install.clicked.connect(do_install)
    b_close.clicked.connect(dlg.reject)
    dlg.show()
    return app.exec_()


# ------------------------------------------------------------------ запуск
def main(argv) -> int:
    silent = any(a.upper() == "/S" for a in argv)
    target = next((a[3:] for a in argv if a.upper().startswith("/D=")), None)
    if "--uninstall-from" in argv:   # копия uninstall.exe из TEMP
        where = argv[argv.index("--uninstall-from") + 1]
        try:
            uninstall(where)
            if not silent:
                message("Программа удалена. Сценарии и настройки пользователей сохранены.")
            return 0
        except Exception as e:
            if not silent:
                message(f"Не удалось удалить программу: {e}", error=True)
            return 1
        finally:
            schedule_self_delete()
    if payload_info() is None:   # uninstall.exe в папке программы
        if not silent and not ask(f"Удалить программу «{TITLE}»?\n\nСценарии и настройки пользователей "
                                  f"останутся."):
            return 0
        relaunch_from_temp(["/S"] if silent else [])
        return 0
    if silent:
        flags = {a.upper() for a in argv}
        try:
            install(target or default_target(), desktop="/NODESKTOP" not in flags,
                    start_menu="/NOSTARTMENU" not in flags)
            return 0
        except Exception as e:
            if sys.stderr:   # в exe без консоли stderr нет — остаётся код возврата
                sys.stderr.write(str(e) + "\n")
            return 1
    return run_gui()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
