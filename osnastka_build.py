"""Сведения о сборке exe (вшиваются в Osnastka.exe; release.py проставляет VERSION).

VERSION — версия кода, встроенного в exe.
RUNTIME — номер набора библиотек сборки (Qt, VTK, PyVista…). Обновление кода (архив Osnastka-update-*.zip)
          ставится, только если ему нужен RUNTIME не больше этого; иначе нужна переустановка программы.
"""
VERSION = "0.4.1"
RUNTIME = 1
