"""SCP Launcher — консольный лаунчер для SCP: Containment Breach (Windows и Linux через Wine)."""

__version__ = "0.1.0"


class LauncherError(Exception):
    """Ожидаемая ошибка, которую CLI показывает пользователю без трейсбека."""
