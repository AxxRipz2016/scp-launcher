# SCP Launcher

Консольный лаунчер для **SCP: Containment Breach** на Python, по мотивам
[PortableMC](https://github.com/theorzr/portablemc). Скачивает и устанавливает
версии игры, держит каждую копию (ванилла, моды) в отдельной папке, правит
`options.ini` и запускает игру: на Windows напрямую, на Linux и macOS через Wine.

Нужен только Python 3.7+, сторонние библиотеки не используются.

## Установка

```sh
pip install .              # появится команда scplauncher
# или без установки, из папки проекта:
python -m scplauncher --help
```

На Linux дополнительно нужен Wine. **Без прав администратора** лаунчер
скачает переносную сборку ([Kron4ek/Wine-Builds](https://github.com/Kron4ek/Wine-Builds),
вариант `amd64-wow64`, которому не нужны 32-битные системные библиотеки) в свою папку:

```sh
python3 -m scplauncher doctor          # проверить компьютер
python3 -m scplauncher wine install    # sudo не нужен
python3 -m scplauncher install vanilla
python3 -m scplauncher start
```

Если GitHub недоступен, скачайте `wine-*-amd64-wow64.tar.xz` на другом компьютере
и выполните `scplauncher wine install --file путь/к/архиву`.

Если права администратора есть, подойдёт и системный Wine (`sudo apt install wine`).
Порядок поиска Wine: `--wine`, затем переменная `SCPL_WINE`, затем переносной Wine
от лаунчера, затем системный `wine`.

## Команды

| Команда | Что делает |
|---|---|
| `scplauncher search [фильтр]` | список версий из манифеста |
| `scplauncher install [версия] [--name ИМЯ]` | скачать и установить (по умолчанию `latest`) |
| `scplauncher import АРХИВ_ИЛИ_ПАПКА --name ИМЯ` | добавить скачанный вручную мод (например с ModDB) |
| `scplauncher list` | установленные инстансы |
| `scplauncher start [ИМЯ] [опции]` | запустить игру |
| `scplauncher config [ИМЯ] [ключ=значение ...]` | показать или изменить `options.ini` |
| `scplauncher remove ИМЯ [-y]` | удалить инстанс (вместе с сохранениями!) |
| `scplauncher doctor` | проверить ОС, Python, права, `noexec`, Wine, доступ к сайтам |
| `scplauncher wine install [--tag 11.21] [--file АРХИВ]` | переносной Wine без sudo (Linux) |
| `scplauncher wine status` / `wine remove` | какой Wine используется / удалить переносной |

Глобальные опции: `--main-dir PATH` (или переменная `SCPL_MAIN_DIR`) и `-v` для подробного вывода.

### Опции `start`

- `--resolution 1920x1080`: разрешение
- `--fullscreen` / `--windowed`: полноэкранный или оконный режим
- `--skip-launcher`: не показывать встроенное окно настроек игры при старте
- `--set ключ=значение` или `--set секция/ключ=значение`: любой параметр `options.ini`
  (можно указать несколько раз; секцию стоит указывать для неоднозначных ключей,
  например `console/enabled=1`)
- `--dry`: показать команду запуска, но не запускать
- `--no-wait`: не ждать закрытия игры
- только для Linux: `--wine PATH` (или `SCPL_WINE`), `--wine-prefix PATH`, `--system-prefix` (взять обычный `~/.wine`)
- только для Linux: `--vram МБ`: сколько видеопамяти Wine сообщает игре (по умолчанию 512, `0` значит не трогать).
  Без этого игра может упасть на 40% загрузки с ошибкой «Memory access violation»: старый движок Blitz3D
  получает отрицательное значение видеопамяти, если её «слишком много».
- На Linux лаунчер также задаёт `PULSE_LATENCY_MSEC=60` (больше звуковой буфер, меньше хрипа).

По умолчанию на Linux используется отдельный префикс Wine `<main-dir>/wine-prefix`,
чтобы не трогать ваш `~/.wine`. Чтобы запустить через Proton или другую сборку Wine,
передайте путь к её бинарнику в `--wine`.

## Примеры

```sh
scplauncher install vanilla
scplauncher start --resolution 1920x1080 --fullscreen

# мод, скачанный вручную
scplauncher import ~/Downloads/SCP_CB_UE_Reborn.zip --name ue-reborn
scplauncher start ue-reborn

scplauncher config vanilla-1.3.11              # посмотреть все настройки
scplauncher config vanilla-1.3.11 vsync=1      # изменить
scplauncher start --resolution 1920x1080 --skip-launcher --set console/enabled=1
```

## Где лежат файлы

```
<main-dir>/                     Windows: %APPDATA%\scplauncher
                                Linux:   ~/.local/share/scplauncher
  instances/<имя>/              копия игры, её Saves/ и options.ini
  cache/<версия>/               скачанные архивы (удаляются после установки,
                                если не указан --keep-archive)
  wine-prefix/                  префикс Wine (Linux/macOS)
  wine/                         переносной Wine (wine install)
  versions.json                 ваши версии (необязательно)
```

## Свои версии

Создайте `<main-dir>/versions.json` в том же формате, что и
[scplauncher/versions.json](scplauncher/versions.json). Версии с тем же `id`
заменят встроенные. Поля `size` и `sha256` необязательны, но если они указаны,
скачанный файл будет проверен.

```json
{
  "aliases": { "ue": "my-mod" },
  "versions": [
    {
      "id": "my-mod",
      "name": "Мой мод",
      "kind": "mod",
      "url": "https://example.com/my-mod.zip",
      "sha256": null,
      "exe": null
    }
  ]
}
```

Для общего списка версий в интернете можно указать `--manifest-url URL`
у команд `search` и `install`.

## Как это устроено

| Модуль | Назначение |
|---|---|
| `manifest.py` | манифест версий: встроенный + пользовательский + по ссылке |
| `download.py` | загрузка с докачкой (HTTP Range), повторами и проверкой размера и SHA-256 |
| `archive.py` | распаковка zip/tar (7z/rar через внешний 7-Zip) с защитой от «zip slip» |
| `instances.py` | инстансы и поиск exe игры |
| `ini.py` | правка `options.ini` с сохранением форматирования |
| `runner.py` | запуск: напрямую или через Wine |
| `wine.py` | скачивание переносного Wine с GitHub |
| `doctor.py` | проверка компьютера |
| `cli.py` | команды |

Тесты: `python -m unittest discover -s tests`

## Лицензия игры

SCP: Containment Breach распространяется под CC BY-SA 3.0 (Undertow Games,
https://www.scpcbgame.com/). Лаунчер ничего не перезаливает, а скачивает игру
с официального адреса.
