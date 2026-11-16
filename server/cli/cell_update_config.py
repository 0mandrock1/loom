"""An explicit nonsecret settings overlay for a cell's target configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from server.settings import Settings
from server.settings._file import SettingsFileError, read_settings_file
from server.settings._live import coerce


class _Target(Settings):
    """Validate the supplied complete model without rereading external sources."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def read_target(path: Path, current: Settings) -> Settings:
    """Overlay FILE on the CLI's effective settings; install neither the file nor values."""
    try:
        values = read_settings_file(str(path), set(Settings.model_fields))
    except (SettingsFileError, UnicodeError, TypeError):
        # Reader messages may quote a version, YAML source, or an arbitrary mapping key.
        raise SettingsFileError(
            f"{path}: invalid target settings file; use version: 1, unique setting names "
            "and nonsecret values"
        ) from None
    checked: dict[str, Any] = {}
    for name, value in values.items():
        try:
            checked[name] = coerce(Settings, name, value)
        except ValidationError:
            raise SettingsFileError(f"{path}: {name} has an invalid target value") from None
    try:
        return _Target(**(current.model_dump() | checked))
    except ValidationError:
        # Validators can embed supplied values in their own messages despite hidden inputs.
        raise SettingsFileError(
            f"{path}: invalid target configuration; check the setting constraints"
        ) from None
