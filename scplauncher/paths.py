"""Где лаунчер хранит свои данные."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "scplauncher"


def default_main_dir() -> Path:
    """Главная папка лаунчера.

    Можно переопределить переменной окружения SCPL_MAIN_DIR или флагом --main-dir.
    - Windows: %APPDATA%/scplauncher
    - macOS:   ~/Library/Application Support/scplauncher
    - Linux:   $XDG_DATA_HOME/scplauncher или ~/.local/share/scplauncher
    """
    env = os.environ.get("SCPL_MAIN_DIR")
    if env:
        return Path(env).expanduser()

    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else Path.home() / ".local" / "share") / APP_NAME


class Layout:
    """Структура главной папки:

    main_dir/
        instances/<имя>/      — установленные копии игры (каждая отдельно)
        cache/<версия>/       — скачанные архивы
        wine-prefix/          — отдельный префикс Wine (только Linux/macOS)
        wine/                 — переносной Wine, скачанный командой "wine install"
        versions.json         — (необязательно) свои версии в дополнение к встроенным
    """

    def __init__(self, main_dir: Path):
        self.main_dir = main_dir
        self.instances_dir = main_dir / "instances"
        self.cache_dir = main_dir / "cache"
        self.wine_prefix = main_dir / "wine-prefix"
        self.wine_dir = main_dir / "wine"
        self.user_manifest = main_dir / "versions.json"


def remove_file(path: Path) -> None:
    """Удалить файл, если он есть (аналог path.unlink(missing_ok=True) для Python 3.7)."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def is_inside(path: Path, root: Path) -> bool:
    """path лежит внутри root (аналог Path.is_relative_to из Python 3.9)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
