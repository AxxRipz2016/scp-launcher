"""Командная строка лаунчера."""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from . import LauncherError, __version__, archive, doctor, instances, runner
from . import wine as wine_mod
from .download import download
from .ini import IniFile
from .manifest import load_manifest
from .paths import Layout, default_main_dir, remove_file

# ---------------------------------------------------------------- вывод


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return str(n)


class ProgressBar:
    def __init__(self, label: str):
        self.label = label
        self.start = time.monotonic()
        self.last = 0.0
        self.tty = sys.stdout.isatty()

    def __call__(self, done: int, total: int | None) -> None:
        now = time.monotonic()
        if now - self.last < 0.2 and done != total:
            return
        self.last = now
        speed = done / max(now - self.start, 1e-6)
        if total:
            pct = done * 100 // total
            width = 30
            filled = width * done // total
            bar = "#" * filled + "-" * (width - filled)
            text = f"{self.label} [{bar}] {pct:3d}%  {_fmt_size(done)} / {_fmt_size(total)}  {_fmt_size(int(speed))}/с"
        else:
            text = f"{self.label} {_fmt_size(done)}  {_fmt_size(int(speed))}/с"
        if self.tty:
            print("\r" + text.ljust(100), end="", flush=True)

    def finish(self) -> None:
        if self.tty:
            print()


def _info(msg: str) -> None:
    print(msg, flush=True)


def _warn(msg: str) -> None:
    print(f"внимание: {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- команды


def cmd_search(args, layout: Layout) -> int:
    manifest = load_manifest(layout.user_manifest, args.manifest_url)
    installed = {i.version for i in instances.list_instances(layout.instances_dir)}

    rows = []
    for v in manifest.versions.values():
        hay = f"{v.id} {v.name} {v.kind}".lower()
        if args.filter and not any(f.lower() in hay for f in args.filter):
            continue
        rows.append(v)

    if not rows:
        _info("ничего не найдено")
        return 0

    w = max(len(v.id) for v in rows)
    print(f"{'ID'.ljust(w)}  {'ТИП':8} {'РАЗМЕР':>9}  {'':10} НАЗВАНИЕ")
    for v in rows:
        mark = "установлена" if v.id in installed else ""
        print(f"{v.id.ljust(w)}  {v.kind:8} {_fmt_size(v.size):>9}  {mark:10} {v.name}")
    if manifest.aliases:
        print()
        print("псевдонимы: " + ", ".join(f"{a} -> {t}" for a, t in manifest.aliases.items()))
    return 0


def cmd_install(args, layout: Layout) -> int:
    manifest = load_manifest(layout.user_manifest, args.manifest_url)
    version = manifest.resolve(args.version)
    name = instances.validate_name(args.name or version.id)

    if (layout.instances_dir / name).exists() and not args.force:
        raise LauncherError(f"инстанс '{name}' уже установлен (переустановка: --force)")

    _info(f"Установка {version.name} -> инстанс '{name}'")
    if version.source:
        _info(f"  источник: {version.source}" + (f", лицензия {version.license}" if version.license else ""))

    archive_path = layout.cache_dir / version.id / version.archive_name
    bar = ProgressBar("  загрузка")
    download(version.url, archive_path, size=version.size, sha256=version.sha256, progress=bar)
    bar.finish()

    _info("  распаковка...")
    staging = layout.instances_dir / f".{name}.new"
    if staging.exists():
        shutil.rmtree(staging)
    layout.instances_dir.mkdir(parents=True, exist_ok=True)
    archive.extract(archive_path, staging)

    inst = instances.create(layout.instances_dir, name, version.id, staging,
                            exe_hint=args.exe or version.exe, force=args.force)

    if not args.keep_archive:
        remove_file(archive_path)

    _info(f"Готово: {inst.dir}")
    _info(f"  exe: {inst.exe}")
    _info(f"Запуск: scplauncher start {inst.name}")
    return 0


def cmd_import(args, layout: Layout) -> int:
    src = Path(args.path).expanduser()
    if not src.exists():
        raise LauncherError(f"нет такого файла или папки: {src}")
    name = instances.validate_name(args.name)

    if (layout.instances_dir / name).exists() and not args.force:
        raise LauncherError(f"инстанс '{name}' уже существует (переустановка: --force)")

    layout.instances_dir.mkdir(parents=True, exist_ok=True)
    staging = layout.instances_dir / f".{name}.new"
    if staging.exists():
        shutil.rmtree(staging)

    if src.is_dir():
        _info(f"Копирование папки {src}...")
        shutil.copytree(src, staging)
    else:
        _info(f"Распаковка {src.name}...")
        archive.extract(src, staging)

    inst = instances.create(layout.instances_dir, name, args.version_label or f"import:{src.name}",
                            staging, exe_hint=args.exe, force=args.force)
    _info(f"Готово: {inst.dir}")
    _info(f"  exe: {inst.exe}")
    _info(f"Запуск: scplauncher start {inst.name}")
    return 0


def cmd_list(args, layout: Layout) -> int:
    items = instances.list_instances(layout.instances_dir)
    if not items:
        _info("нет установленных версий (scplauncher install vanilla)")
        return 0
    w = max(len(i.name) for i in items)
    for i in items:
        ok = "" if i.exe_path.is_file() else "  [exe не найден!]"
        print(f"{i.name.ljust(w)}  {i.version}  {i.dir}{ok}")
    return 0


def _parse_resolution(text: str) -> tuple[int, int]:
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError("формат: ШИРИНАxВЫСОТА, например 1920x1080") from None


def _parse_kv(text: str) -> tuple[str | None, str, str]:
    """'ключ=значение' или 'секция/ключ=значение' -> (секция, ключ, значение)."""
    if "=" not in text:
        raise argparse.ArgumentTypeError("формат: ключ=значение или секция/ключ=значение")
    k, v = text.split("=", 1)
    section = None
    if "/" in k:
        section, k = k.split("/", 1)
        section = section.strip()
    return section, k.strip(), v.strip()


def _apply_settings(inst: instances.Instance, settings: list[tuple[str | None, str, str]],
                    default_section: str | None = None) -> None:
    if not settings:
        return
    ini = IniFile(inst.options_ini)
    for section, key, value in settings:
        existed = ini.set(key, value, section or default_section)
        if not existed:
            _warn(f"ключа '{key}' не было в options.ini, он добавлен")
    ini.save()


def cmd_start(args, layout: Layout) -> int:
    inst = instances.get_instance(layout.instances_dir, args.instance)
    if not inst.exe_path.is_file():
        raise LauncherError(f"не найден exe игры: {inst.exe_path}")

    settings: list[tuple[str | None, str, str]] = []
    if args.resolution:
        w, h = args.resolution
        settings += [("options", "width", str(w)), ("options", "height", str(h))]
    if args.fullscreen is not None:
        settings.append(("options", "fullscreen", "true" if args.fullscreen else "false"))
    if args.skip_launcher:
        # Отключает встроенное окно выбора разрешения, которое игра показывает при старте.
        settings.append(("launcher", "launcher enabled", "false"))
    settings += args.set or []
    _apply_settings(inst, settings)

    prefix = None
    if runner.needs_wine() and not args.system_prefix:
        prefix = Path(args.wine_prefix).expanduser() if args.wine_prefix else layout.wine_prefix
    new_prefix = prefix is not None and not prefix.exists()

    managed = wine_mod.managed_wine(layout.wine_dir)
    cmd, _env = runner.build_command(inst.exe_path, args.game_args, args.wine, prefix, managed)
    _info(f"Запуск '{inst.name}'")
    if args.verbose or args.dry:
        _info("  команда: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        _info(f"  папка:   {inst.exe_path.parent}")
        if prefix:
            _info(f"  WINEPREFIX={prefix}")
    if args.dry:
        return 0

    if runner.needs_wine() and args.vram > 0:
        if new_prefix:
            _info(f"Создаётся префикс Wine в {prefix} (первый запуск может занять минуту)...")
        if runner.ensure_video_memory(cmd[0], _env, prefix, args.vram):
            _info(f"  Wine: игре сообщается {args.vram} МБ видеопамяти (защита от вылета при загрузке)")

    code = runner.launch(inst.exe_path, args.game_args, args.wine, prefix,
                         wait=not args.no_wait, managed=managed)
    if code is not None:
        _info(f"Игра завершилась с кодом {code}")
    return 0


def cmd_config(args, layout: Layout) -> int:
    inst = instances.get_instance(layout.instances_dir, args.instance)
    if args.pairs:
        _apply_settings(inst, args.pairs, args.section)
        _info(f"сохранено: {inst.options_ini}")
        return 0

    ini = IniFile(inst.options_ini)
    items = ini.items()
    if not items:
        _info(f"{inst.options_ini} пока пуст или отсутствует (игра создаст его при первом запуске)")
        return 0
    current = None
    for section, key, value in items:
        if args.section and section.lower() != args.section.lower():
            continue
        if section != current:
            print(f"[{section}]")
            current = section
        print(f"  {key} = {value}")
    return 0


def cmd_remove(args, layout: Layout) -> int:
    inst = instances.get_instance(layout.instances_dir, args.instance)
    if not args.yes:
        if not sys.stdin.isatty():
            raise LauncherError("для удаления без вопроса используйте -y")
        answer = input(f"Удалить '{inst.name}' вместе с сохранениями ({inst.dir})? [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "д", "да"):
            _info("отменено")
            return 0
    instances.remove(inst)
    _info(f"удалено: {inst.name}")
    return 0


def cmd_wine_install(args, layout: Layout) -> int:
    bar = ProgressBar("  загрузка")
    binary = wine_mod.install(layout.wine_dir, layout.cache_dir, tag=args.tag,
                              file=Path(args.file).expanduser() if args.file else None,
                              progress=bar, on_info=_info)
    bar.finish()
    _info(f"Wine установлен: {binary}")
    _info("Теперь он будет использоваться автоматически. Проверка: scplauncher doctor")
    return 0


def cmd_wine_status(args, layout: Layout) -> int:
    if not runner.needs_wine():
        _info("Windows: Wine не нужен, игра запускается напрямую")
        return 0
    managed = wine_mod.managed_wine(layout.wine_dir)
    try:
        path = runner.find_wine(args.wine, managed)
    except LauncherError:
        _info("Wine не найден. Установите без sudo: scplauncher wine install")
        return 1
    source = "переносной (от лаунчера)" if managed and path == str(managed) else "системный/указанный"
    _info(f"{path}  [{source}]")
    return 0


def cmd_wine_remove(args, layout: Layout) -> int:
    if wine_mod.remove(layout.wine_dir):
        _info(f"удалено: {layout.wine_dir}")
    else:
        _info("переносной Wine не установлен")
    return 0


def cmd_doctor(args, layout: Layout) -> int:
    checks = doctor.run(layout, args.wine)
    width = max(len(c.title) for c in checks)
    for c in checks:
        print(f"[{c.status}] {c.title.ljust(width)}  {c.detail}")
        if c.advice:
            print(f"     {' ' * width}  -> {c.advice}")
    failed = sum(c.status == doctor.FAIL for c in checks)
    warned = sum(c.status == doctor.WARN for c in checks)
    print()
    if failed:
        print(f"Есть проблемы ({failed}), из-за которых игра не запустится. См. подсказки выше.")
        return 1
    if warned:
        print(f"Есть предупреждения ({warned}). Выполните подсказки, и можно играть.")
    else:
        print("Всё в порядке, можно играть: scplauncher start")
    return 0


# ---------------------------------------------------------------- парсер


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scplauncher",
        description="Лаунчер SCP: Containment Breach. На Linux игра запускается через Wine.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--main-dir", metavar="PATH",
                   help="папка лаунчера (по умолчанию: %(default)s; или переменная SCPL_MAIN_DIR)",
                   default=str(default_main_dir()))
    p.add_argument("-v", "--verbose", action="store_true", help="подробный вывод и трейсбеки ошибок")
    sub = p.add_subparsers(dest="command", required=True, metavar="КОМАНДА")

    s = sub.add_parser("search", help="список доступных версий")
    s.add_argument("filter", nargs="*", help="фильтр по id/названию")
    s.add_argument("--manifest-url", metavar="URL", help="дополнительный манифест версий по ссылке")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("install", help="скачать и установить версию")
    s.add_argument("version", nargs="?", default="latest", help="id версии или псевдоним (по умолчанию latest)")
    s.add_argument("--name", help="имя инстанса (по умолчанию = id версии)")
    s.add_argument("--exe", help="путь к exe внутри архива, если не определился сам")
    s.add_argument("--force", action="store_true", help="переустановить (сохранения и options.ini переносятся)")
    s.add_argument("--keep-archive", action="store_true", help="не удалять скачанный архив из кэша")
    s.add_argument("--manifest-url", metavar="URL", help="дополнительный манифест версий по ссылке")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("import", help="добавить скачанную вручную версию или мод (архив или папку)")
    s.add_argument("path", help="архив (.zip, .tar.*, .7z/.rar при наличии 7-Zip) или папка с игрой")
    s.add_argument("--name", required=True, help="имя инстанса, например ue-reborn")
    s.add_argument("--exe", help="путь к exe внутри, если не определился сам")
    s.add_argument("--version-label", help="подпись версии для списка")
    s.add_argument("--force", action="store_true", help="заменить существующий инстанс")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("list", help="установленные версии")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("start", help="запустить игру")
    s.add_argument("instance", nargs="?", help="имя инстанса (можно не указывать, если он один)")
    s.add_argument("--resolution", type=_parse_resolution, metavar="WxH", help="разрешение, например 1920x1080")
    fs = s.add_mutually_exclusive_group()
    fs.add_argument("--fullscreen", dest="fullscreen", action="store_true", default=None, help="полноэкранный режим")
    fs.add_argument("--windowed", dest="fullscreen", action="store_false", help="оконный режим")
    s.add_argument("--skip-launcher", action="store_true",
                   help="отключить встроенное окно настроек игры при старте (сохраняется в options.ini)")
    s.add_argument("--set", type=_parse_kv, action="append", metavar="КЛЮЧ=ЗНАЧЕНИЕ",
                   help="изменить параметр options.ini перед запуском, можно 'секция/ключ=значение' (несколько раз)")
    s.add_argument("--dry", action="store_true", help="показать команду запуска, но не запускать")
    s.add_argument("--no-wait", action="store_true", help="не ждать закрытия игры")
    s.add_argument("--wine", metavar="PATH", help="[Linux] путь к wine (или переменная SCPL_WINE)")
    s.add_argument("--wine-prefix", metavar="PATH", help="[Linux] свой WINEPREFIX")
    s.add_argument("--system-prefix", action="store_true", help="[Linux] использовать обычный ~/.wine")
    s.add_argument("--vram", type=int, default=512, metavar="МБ",
                   help="[Linux] сколько видеопамяти Wine сообщает игре (по умолчанию %(default)s; "
                        "0 — не трогать). Защищает от 'Memory access violation' при загрузке")
    s.add_argument("game_args", nargs="*", help="аргументы для игры (после --)")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("config", help="показать или изменить options.ini инстанса")
    s.add_argument("instance", nargs="?", help="имя инстанса")
    s.add_argument("pairs", nargs="*", type=_parse_kv, metavar="КЛЮЧ=ЗНАЧЕНИЕ")
    s.add_argument("--section", help="секция ini (по умолчанию ищется по всем)")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("remove", help="удалить инстанс")
    s.add_argument("instance", help="имя инстанса")
    s.add_argument("-y", "--yes", action="store_true", help="не спрашивать подтверждение")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("wine", help="[Linux] переносной Wine без прав администратора")
    wsub = s.add_subparsers(dest="wine_command", required=True, metavar="ДЕЙСТВИЕ")
    w = wsub.add_parser("install", help="скачать и распаковать Wine в папку лаунчера")
    w.add_argument("--tag", help="версия сборки, например 11.21 (по умолчанию последняя)")
    w.add_argument("--file", metavar="PATH",
                   help="взять уже скачанный архив wine-*-amd64-wow64.tar.xz (например с флешки)")
    w.set_defaults(func=cmd_wine_install)
    w = wsub.add_parser("status", help="какой Wine будет использован")
    w.add_argument("--wine", metavar="PATH", help="проверить конкретный wine")
    w.set_defaults(func=cmd_wine_status)
    w = wsub.add_parser("remove", help="удалить переносной Wine")
    w.set_defaults(func=cmd_wine_remove)

    s = sub.add_parser("doctor", help="проверить, получится ли здесь установить и запустить игру")
    s.add_argument("--wine", metavar="PATH", help="проверить конкретный wine")
    s.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except AttributeError:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    # "config ИМЯ ключ=знач": если первый позиционный аргумент похож на ключ=значение,
    # значит имя инстанса не указано.
    if args.command == "config" and args.instance and "=" in args.instance:
        args.pairs = [_parse_kv(args.instance), *args.pairs]
        args.instance = None

    layout = Layout(Path(args.main_dir).expanduser())
    try:
        return args.func(args, layout)
    except LauncherError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nпрервано", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"непредвиденная ошибка: {e!r} (подробнее: -v)", file=sys.stderr)
        return 1
