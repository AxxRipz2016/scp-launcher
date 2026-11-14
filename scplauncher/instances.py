"""Инстансы: отдельные установленные копии игры (ванилла, моды и т.д.)."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import LauncherError

META_FILE = "scplauncher-instance.json"
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Эти файлы/папки переносятся при переустановке (--force), чтобы не потерять прогресс.
PRESERVE_ON_REINSTALL = ("Saves", "options.ini")

# Exe-файлы, которые точно не являются игрой.
EXE_BLACKLIST = ("unins", "setup", "vcredist", "dxsetup", "dxwebsetup", "mapcreator",
                 "map creator", "crashreport")


@dataclass
class Instance:
    name: str
    version: str
    exe: str  # путь к exe относительно папки инстанса
    created: str
    dir: Path = None  # type: ignore[assignment]

    @property
    def exe_path(self) -> Path:
        return self.dir / self.exe

    @property
    def options_ini(self) -> Path:
        # Игра хранит options.ini рядом со своим exe.
        return self.exe_path.parent / "options.ini"

    def save_meta(self) -> None:
        data = asdict(self)
        data.pop("dir")
        (self.dir / META_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                          encoding="utf-8")


def validate_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise LauncherError(
            f"недопустимое имя '{name}': используйте латиницу, цифры, точку, '-' и '_'"
        )
    return name


def list_instances(instances_dir: Path) -> list[Instance]:
    if not instances_dir.is_dir():
        return []
    result = []
    for d in sorted(instances_dir.iterdir()):
        meta = d / META_FILE
        if d.is_dir() and meta.is_file():
            result.append(_load(d))
    return result


def get_instance(instances_dir: Path, name: str | None) -> Instance:
    if name is None:
        all_ = list_instances(instances_dir)
        if len(all_) == 1:
            return all_[0]
        if not all_:
            raise LauncherError("нет установленных версий. Сначала: scplauncher install vanilla")
        raise LauncherError("установлено несколько версий, укажите имя (scplauncher list)")

    d = instances_dir / name
    if not (d / META_FILE).is_file():
        raise LauncherError(f"инстанс '{name}' не найден (scplauncher list)")
    return _load(d)


def _load(d: Path) -> Instance:
    data = json.loads((d / META_FILE).read_text(encoding="utf-8"))
    return Instance(name=data["name"], version=data["version"], exe=data["exe"],
                    created=data["created"], dir=d)


def find_exe(game_dir: Path, hint: str | None = None) -> str:
    """Найти exe игры внутри папки. hint — точный путь из манифеста или от пользователя."""
    if hint:
        if (game_dir / hint).is_file():
            return Path(hint).as_posix()
        raise LauncherError(f"в папке игры нет файла {hint}")

    best: tuple[int, str] | None = None
    for p in game_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() != ".exe":
            continue
        low = p.name.lower()
        if any(bad in low for bad in EXE_BLACKLIST):
            continue
        rel = p.relative_to(game_dir)
        score = 0
        if "containment breach" in low:
            score += 20
        if "scp" in low:
            score += 10
        score -= 5 * (len(rel.parts) - 1)  # чем глубже, тем менее вероятно
        if best is None or score > best[0]:
            best = (score, rel.as_posix())

    if best is None:
        raise LauncherError(f"в {game_dir} не найден ни один .exe игры (укажите --exe)")
    return best[1]


def create(instances_dir: Path, name: str, version: str, source_dir: Path,
           exe_hint: str | None = None, force: bool = False) -> Instance:
    """Превратить готовую папку с игрой (source_dir) в инстанс; source_dir перемещается."""
    validate_name(name)
    instances_dir.mkdir(parents=True, exist_ok=True)
    target = instances_dir / name

    exe = find_exe(source_dir, exe_hint)

    if target.exists():
        if not force:
            shutil.rmtree(source_dir, ignore_errors=True)
            raise LauncherError(f"инстанс '{name}' уже существует (используйте --force или --name)")
        _carry_over_user_data(target, source_dir)
        shutil.rmtree(target)

    source_dir.rename(target)
    inst = Instance(name=name, version=version, exe=exe,
                    created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    dir=target)
    inst.save_meta()
    return inst


def _carry_over_user_data(old: Path, new: Path) -> None:
    for item in PRESERVE_ON_REINSTALL:
        for src in old.rglob(item):
            rel = src.relative_to(old)
            dst = new / rel
            if dst.exists():
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))


def remove(inst: Instance) -> None:
    shutil.rmtree(inst.dir)
