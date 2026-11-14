"""Запуск игры: на Windows напрямую, на Linux/macOS через Wine."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import LauncherError


def needs_wine() -> bool:
    return sys.platform != "win32"


def find_wine(explicit: str | None = None, managed: Path | None = None) -> str:
    """Порядок поиска: --wine, SCPL_WINE, Wine от лаунчера, системный wine."""
    candidate = explicit or os.environ.get("SCPL_WINE")
    if candidate:
        path = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if path is None:
            raise LauncherError(f"Wine не найден по пути {candidate}")
        return path
    if managed is not None:
        return str(managed)
    for name in ("wine", "wine64"):
        path = shutil.which(name)
        if path:
            return path
    raise LauncherError(
        "для запуска на Linux нужен Wine. Без прав администратора:\n"
        "  scplauncher wine install\n"
        "или через пакетный менеджер (нужен sudo): sudo apt install wine\n"
        "или укажите свой путь: --wine /путь/к/wine"
    )


def build_command(exe: Path, args: list[str], wine: str | None = None,
                  wine_prefix: Path | None = None,
                  managed: Path | None = None) -> tuple[list[str], dict[str, str]]:
    """Собрать команду запуска и переменные окружения."""
    env = dict(os.environ)
    if not needs_wine():
        return [str(exe), *args], env

    wine_bin = find_wine(wine, managed)
    if wine_prefix is not None:
        wine_prefix.mkdir(parents=True, exist_ok=True)
        env["WINEPREFIX"] = str(wine_prefix)
    env.setdefault("WINEDEBUG", "-all")  # убрать спам отладочных сообщений Wine
    # Не предлагать установить Mono/Gecko при создании префикса: игре они не нужны.
    env.setdefault("WINEDLLOVERRIDES", "mscoree,mshtml=")
    # Больший звуковой буфер PulseAudio/PipeWire убирает хрип и заикание звука.
    env.setdefault("PULSE_LATENCY_MSEC", "60")
    return [wine_bin, str(exe), *args], env


def ensure_video_memory(wine_bin: str, env: dict[str, str], prefix: Path | None,
                        megabytes: int) -> bool:
    """Сообщать игре фиксированный объём видеопамяти.

    SCP:CB (Blitz3D) хранит объём VRAM в 32-битном знаковом числе. Если Wine
    сообщает много памяти, число переполняется и становится отрицательным
    ("Total video memory (MB): -8"), после чего игра падает при загрузке с
    "Memory access violation". Значение пишется в реестр префикса один раз.
    Возвращает True, если реестр был изменён.
    """
    marker = prefix / f".scplauncher-vram-{megabytes}" if prefix else None
    if marker is not None and marker.exists():
        return False
    result = subprocess.run(
        [wine_bin, "reg", "add", r"HKCU\Software\Wine\Direct3D",
         "/v", "VideoMemorySize", "/t", "REG_SZ", "/d", str(megabytes), "/f"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    if result.returncode != 0:
        raise LauncherError(f"не удалось настроить видеопамять в Wine: {result.stderr.strip()}")
    if marker is not None:
        for old in prefix.glob(".scplauncher-vram-*"):  # type: ignore[union-attr]
            old.unlink()
        marker.touch()
    return True


def launch(exe: Path, args: list[str], wine: str | None = None,
           wine_prefix: Path | None = None, wait: bool = True,
           managed: Path | None = None) -> int | None:
    cmd, env = build_command(exe, args, wine, wine_prefix, managed)
    # Рабочая папка = папка игры: Blitz3D-игры ищут свои файлы по относительным путям.
    try:
        proc = subprocess.Popen(cmd, cwd=exe.parent, env=env)
    except OSError as e:
        raise LauncherError(f"не удалось запустить игру: {e}") from None
    if not wait:
        return None
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()
