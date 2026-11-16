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
    values = read_settings_file(str(path), set(Settings.model_fields))
    checked: dict[str, Any] = {}
    for name, value in values.items():
        try:
            checked[name] = coerce(Settings, name, value)
        except ValidationError as error:
            rule = error.errors(include_input=False)[0]["msg"]
            raise SettingsFileError(f"{path}: {name} has an invalid value ({rule})") from None
    try:
        return _Target(**(current.model_dump() | checked))
    except ValidationError as error:
        # Settings hides inputs, including a model-level validator's complete input.
        raise SettingsFileError(f"{path}: invalid target configuration ({error})") from None
