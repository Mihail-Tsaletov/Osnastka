"""Точка входа для сборки exe (PyInstaller)."""
import sys

from weld_viz import updater

if __name__ == "__main__":
    # запуск новой версии в роли установщика обновления — без интерфейса
    if not updater.handle_apply_flag(sys.argv):
        updater.cleanup()
        from weld_viz.app import main
        main()
