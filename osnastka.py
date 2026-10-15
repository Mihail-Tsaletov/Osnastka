"""Точка входа Osnastka.exe — загрузчик кода программы.

Сам exe после установки не меняется: его ставит администратор, писать в папку программы и запускать другие
программы пользователю нельзя. Обновления кода приходят архивом Osnastka-update-<версия>.zip, программа
распаковывает его в %LOCALAPPDATA%\\Osnastka\\app\\<версия> (weld_viz/updater.py), а этот загрузчик при запуске
подключает самый свежий совместимый код. Если обновлённый код не запускается — возвращается к встроенному.

Только стандартная библиотека: этот файл вшит в exe навсегда.
Режимы: Osnastka.exe [сборка.prt | сценарий.yaml]; Osnastka.exe --pdf <сценарий.yaml> <файл.pdf>
"""
import importlib.machinery
import json
import os
import re
import sys
import time
import traceback

import osnastka_build as build

APPDATA = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Osnastka")
CODE_ROOT = os.path.join(APPDATA, "app")
CURRENT = os.path.join(CODE_ROOT, "current.json")
ERROR_LOG = os.path.join(APPDATA, "last_error.txt")


def _ver(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _message(text, title="Закладка в оснастку"):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def _log_error(text):
    try:
        os.makedirs(APPDATA, exist_ok=True)
        with open(ERROR_LOG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n" + text)
    except OSError:
        pass


def _read_current():
    try:
        with open(CURRENT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _mark_broken(cur, reason):
    cur = dict(cur, broken=True, broken_reason=reason)
    try:
        with open(CURRENT, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _updated_code_dir():
    """Папка обновлённого кода, если он новее встроенного, совместим и не помечен сломанным."""
    cur = _read_current()
    if not cur or cur.get("broken"):
        return None, cur
    d = os.path.join(CODE_ROOT, str(cur.get("dir", "")))
    if int(cur.get("runtime", 999)) > build.RUNTIME or _ver(cur.get("version", "0")) <= _ver(build.VERSION):
        return None, cur
    if not os.path.isfile(os.path.join(d, "weld_viz", "__init__.py")):
        return None, cur
    return d, cur


class _CodeFinder:
    """Пакет weld_viz — из папки обновлённого кода (раньше встроенного в exe)."""

    def __init__(self, code_dir):
        self.code_dir = code_dir

    def find_spec(self, name, path=None, target=None):
        if name == "weld_viz":
            return importlib.machinery.PathFinder.find_spec(name, [self.code_dir])
        if name.startswith("weld_viz."):
            return importlib.machinery.PathFinder.find_spec(name, path)
        return None

    def invalidate_caches(self):
        pass


def _purge():
    for name in [n for n in sys.modules if n == "weld_viz" or n.startswith("weld_viz.")]:
        del sys.modules[name]


def _load_code():
    """Подключить обновлённый код (если есть) и проверить, что он загружается; иначе — встроенный."""
    code_dir, cur = _updated_code_dir()
    if not code_dir:
        return
    finder = _CodeFinder(code_dir)
    sys.meta_path.insert(0, finder)
    os.environ["OSNASTKA_CODE_DIR"] = code_dir
    try:
        __import__("weld_viz.app")   # проверка: обновлённый код и все его модули загружаются
    except Exception:
        tb = traceback.format_exc()
        sys.meta_path.remove(finder)
        os.environ.pop("OSNASTKA_CODE_DIR", None)
        _purge()
        _mark_broken(cur, tb[-1500:])
        _log_error(tb)
        _message(f"Обновление {cur.get('version')} не запускается — программа вернулась к встроенной версии "
                 f"{build.VERSION}.\n\nПодробности: {ERROR_LOG}")


def main():
    _load_code()
    if len(sys.argv) >= 4 and sys.argv[1] == "--pdf":   # пакетная печать PDF без окна
        from weld_viz import cli
        cli.main([sys.argv[2], "-o", sys.argv[3]])
        return
    from weld_viz.app import main as app_main
    app_main()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _log_error(tb)
        _message("Программа завершилась с ошибкой:\n\n" + tb[-1200:] + f"\n\nПодробности: {ERROR_LOG}")
        sys.exit(1)
