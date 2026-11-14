"""Манифест версий: встроенный versions.json + пользовательский + (необязательно) удалённый."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import LauncherError, __version__

BUILTIN_MANIFEST = Path(__file__).with_name("versions.json")
USER_AGENT = f"scplauncher/{__version__}"


@dataclass
class Version:
    id: str
    name: str
    url: str
    kind: str = "mod"
    size: int | None = None
    sha256: str | None = None
    exe: str | None = None
    source: str | None = None
    license: str | None = None
    description: str | None = None

    @property
    def archive_name(self) -> str:
        """Имя файла архива, взятое из URL."""
        path = urllib.parse.urlparse(self.url).path
        name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
        return name or f"{self.id}.zip"

    @classmethod
    def from_dict(cls, data: dict) -> "Version":
        try:
            return cls(
                id=data["id"],
                name=data.get("name", data["id"]),
                url=data["url"],
                kind=data.get("kind", "mod"),
                size=data.get("size"),
                sha256=(data.get("sha256") or None),
                exe=data.get("exe"),
                source=data.get("source"),
                license=data.get("license"),
                description=data.get("description"),
            )
        except KeyError as e:
            raise LauncherError(f"в манифесте у версии нет обязательного поля {e}") from None


class Manifest:
    def __init__(self) -> None:
        self.versions: dict[str, Version] = {}
        self.aliases: dict[str, str] = {}

    def merge(self, data: dict) -> None:
        """Добавить версии; версии с тем же id заменяются."""
        for raw in data.get("versions", []):
            v = Version.from_dict(raw)
            self.versions[v.id] = v
        self.aliases.update(data.get("aliases", {}))

    def resolve(self, name: str) -> Version:
        real = self.aliases.get(name, name)
        try:
            return self.versions[real]
        except KeyError:
            raise LauncherError(
                f"версия '{name}' не найдена в манифесте (список версий: scplauncher search)"
            ) from None


def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise LauncherError(f"ошибка в JSON-файле {path}: {e}") from None


def _read_json_url(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise LauncherError(f"не удалось загрузить манифест {url}: {e}") from None


def load_manifest(user_manifest: Path | None = None, extra_url: str | None = None) -> Manifest:
    m = Manifest()
    m.merge(_read_json_file(BUILTIN_MANIFEST))
    if user_manifest is not None and user_manifest.is_file():
        m.merge(_read_json_file(user_manifest))
    if extra_url:
        m.merge(_read_json_url(extra_url))
    return m
