__version__ = "0.4.0"

# Qt: PySide6 (Windows 10/11) или PySide2 (сборка для Windows 7); модули импортируют Qt через qtpy
import importlib.util as _ilu  # noqa: E402
import os as _os  # noqa: E402

if "QT_API" not in _os.environ:
    _os.environ["QT_API"] = "pyside6" if _ilu.find_spec("PySide6") else "pyside2"
