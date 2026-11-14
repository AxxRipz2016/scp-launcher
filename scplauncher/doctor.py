"""Проверка компьютера: получится ли здесь установить и запустить игру."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import LauncherError, instances, runner
from . import wine as wine_mod
from .manifest import USER_AGENT
from .paths import Layout, remove_file

OK, WARN, FAIL = "OK", "!!", "XX"
INFO = "--"  # просто сведения: на запуск SCP:CB не влияет

FLATHUB_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"

# Ванильная игра (~235 МБ архив + ~330 МБ распакованная) и Wine (~95 МБ архив + ~800 МБ).
NEEDED_SPACE = 1600 * 2**20


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""
    advice: str = ""


def _reachable(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        # Сервер ответил — значит, доступ есть (некоторые не любят HEAD).
        return e.code < 500 and e.code not in (403, 451), f"HTTP {e.code}"
    except OSError as e:
        reason = getattr(e, "reason", e)
        return False, str(reason)


def _exec_allowed(directory: Path) -> tuple[bool, str]:
    """Проверить, разрешён ли запуск программ из папки (нет ли noexec)."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="scpl-test-", suffix=".sh", dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("#!/bin/sh\necho ok\n")
        path.chmod(0o755)
        out = subprocess.run([str(path)], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() == "ok", (out.stderr.strip() or "ok")
    except OSError as e:
        return False, str(e)
    finally:
        remove_file(path)


def _linux_distro() -> str | None:
    """Название дистрибутива из /etc/os-release (например "RED OS 8")."""
    try:
        text = Path("/etc/os-release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip().strip('"')
    name = fields.get("PRETTY_NAME") or fields.get("NAME")
    return f"{name} (ядро {platform.release()})" if name else None


def _flatpak_check() -> Check:
    title = "Flatpak (для Steam/Sober)"
    exe = shutil.which("flatpak")
    if exe is None:
        return Check(INFO, title, "не установлен",
                     "без прав администратора не поставить; Steam и Sober через Flatpak недоступны "
                     "(SCP:CB это не мешает)")
    try:
        ver = subprocess.run([exe, "--version"], capture_output=True, text=True,
                             timeout=20).stdout.strip()
        remotes = subprocess.run([exe, "remotes", "--columns=name,options"],
                                 capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return Check(WARN, title, f"{exe}: {e}", "Flatpak есть, но не запускается")

    found = [line.split() for line in remotes.splitlines() if line.strip()]
    flathub = [r for r in found if r[0].lower() == "flathub"]
    if flathub:
        where = ", ".join(r[1] if len(r) > 1 else "?" for r in flathub)
        return Check(OK, title, f"{ver}; Flathub подключён ({where})")
    return Check(OK, title, f"{ver}; Flathub не подключён",
                 "подключить для себя без sudo: flatpak remote-add --user --if-not-exists "
                 "flathub https://dl.flathub.org/repo/flathub.flatpakrepo")


def run(layout: Layout, wine_override: str | None = None) -> list[Check]:
    checks: list[Check] = []
    linux = runner.needs_wine()

    # --- система
    machine = platform.machine().lower()
    distro = _linux_distro() if linux else None
    system = distro or f"{platform.system()} {platform.release()}"
    checks.append(Check(OK, "Система", f"{system}, {machine}"))
    if linux and machine not in ("x86_64", "amd64"):
        checks.append(Check(FAIL, "Архитектура", machine,
                            "переносной Wine есть только для 64-битных x86 (x86_64)"))

    py = sys.version_info
    checks.append(Check(OK if py >= (3, 7) else FAIL, "Python",
                        platform.python_version(),
                        "" if py >= (3, 7) else "нужен Python 3.7 или новее"))

    # --- место и права
    try:
        layout.main_dir.mkdir(parents=True, exist_ok=True)
        probe = layout.main_dir / ".scpl-write-test"
        probe.write_text("x")
        probe.unlink()
        free = shutil.disk_usage(layout.main_dir).free
        enough = free >= NEEDED_SPACE
        checks.append(Check(OK if enough else WARN, "Папка лаунчера",
                            f"{layout.main_dir} (свободно {free / 2**30:.1f} ГБ)",
                            "" if enough else "нужно около 1.6 ГБ свободного места"))
    except OSError as e:
        checks.append(Check(FAIL, "Папка лаунчера", f"{layout.main_dir}: {e}",
                            "нет прав на запись; укажите другую папку: --main-dir ПУТЬ"))
        return checks

    if linux:
        ok, detail = _exec_allowed(layout.main_dir)
        checks.append(Check(OK if ok else FAIL, "Запуск программ из папки лаунчера", detail,
                            "" if ok else "папка смонтирована с noexec или запуск запрещён "
                            "политикой. Попробуйте другую папку (--main-dir), например на флешке"))

    # --- Wine
    if linux:
        managed = wine_mod.managed_wine(layout.wine_dir)
        try:
            wine_bin = runner.find_wine(wine_override, managed)
        except LauncherError:
            wine_bin = None
        if wine_bin is None:
            checks.append(Check(WARN, "Wine", "не найден",
                                "выполните: scplauncher wine install (sudo не нужен)"))
        else:
            try:
                out = subprocess.run([wine_bin, "--version"], capture_output=True,
                                     text=True, timeout=60)
                ver = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
                if out.returncode == 0:
                    checks.append(Check(OK, "Wine", f"{ver} ({wine_bin})"))
                else:
                    checks.append(Check(FAIL, "Wine", f"{wine_bin}: код {out.returncode} {ver}",
                                        "Wine не запускается; возможно, системе не хватает библиотек "
                                        "или запуск неподписанных программ запрещён"))
            except (OSError, subprocess.TimeoutExpired) as e:
                checks.append(Check(FAIL, "Wine", f"{wine_bin}: {e}",
                                    "Wine не запускается (запрет запуска программ?)"))

        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            checks.append(Check(WARN, "Графический сеанс", "нет DISPLAY/WAYLAND_DISPLAY",
                                "игру нужно запускать из терминала внутри рабочего стола, не по SSH"))

        # Flatpak для SCP:CB не нужен, но через него без sudo ставятся Steam и Sober (Roblox).
        checks.append(_flatpak_check())

    # --- сеть
    sites = [
        ("Сайт игры (undertowgames.com)",
         "https://undertowgames.com/file/scp/SCP%20-%20Containment%20Breach%20v1.3.11.zip"),
        ("GitHub API", "https://api.github.com/"),
    ]
    if linux:
        sites.append(("GitHub (загрузка Wine)", f"https://github.com/{wine_mod.REPO}/releases"))
    for title, url in sites:
        ok, detail = _reachable(url)
        checks.append(Check(OK if ok else WARN, title, detail,
                            "" if ok else "сайт недоступен: скачайте файлы на другом компьютере "
                            "и используйте import / wine install --file"))
    if linux:
        ok, detail = _reachable(FLATHUB_URL)
        checks.append(Check(OK if ok else INFO, "Flathub (dl.flathub.org)", detail,
                            "" if ok else "Flathub закрыт в этой сети: приложения Flatpak не скачать"))

    # --- установленные версии
    insts = instances.list_instances(layout.instances_dir)
    if not insts:
        checks.append(Check(WARN, "Игра", "не установлена", "выполните: scplauncher install vanilla"))
    for inst in insts:
        ok = inst.exe_path.is_file()
        checks.append(Check(OK if ok else FAIL, f"Игра '{inst.name}'",
                            inst.exe if ok else f"нет файла {inst.exe_path}",
                            "" if ok else "переустановите: scplauncher install --force"))
    return checks
