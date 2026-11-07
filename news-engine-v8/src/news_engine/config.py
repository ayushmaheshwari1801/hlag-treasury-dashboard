"""Local credentials only; runtime settings are ordinary function arguments."""

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


@dataclass(frozen=True)
class Credentials:
    llm_api_key: str = field(repr=False)
    llm_model: str
    llm_base_url: str
    exa_api_key: str | None = field(default=None, repr=False)


def validate_base_url(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment
            or any(char.isspace() or ord(char) < 32 for char in value)):
        raise ValueError("LLM_BASE_URL must be an HTTPS URL without credentials, query or fragment")
    parts.port
    return value.rstrip("/")


def load_credentials(env_file: Path = Path(".env"), *,
                     environ: Mapping[str, str] | None = None,
                     require_exa: bool = True) -> Credentials:
    path = Path(env_file)
    local = dotenv_values(path, interpolate=False, encoding="utf-8-sig") if path.is_file() else {}
    environment = os.environ if environ is None else environ

    def setting(name, alias=None):
        for layer in (environment, local):
            for key in (name, alias):
                if key is not None and key in layer:
                    return layer[key]
        return None

    def key(name, alias=None, required=True):
        value = setting(name, alias)
        if value in (None, "") and not required:
            return None
        if not isinstance(value, str) or not value or any(char.isspace() for char in value):
            raise ValueError(f"{name} must be configured without whitespace")
        return value

    model = setting("LLM_MODEL", "OPENROUTER_MODEL")
    if not isinstance(model, str) or not model.strip() or any(ord(c) < 32 for c in model):
        raise ValueError("LLM_MODEL must be configured")
    return Credentials(
        llm_api_key=key("OPENROUTER_API_KEY", "LLM_API_KEY"),
        llm_model=model.strip(),
        llm_base_url=validate_base_url(setting("LLM_BASE_URL", "OPENROUTER_BASE_URL")
                                      or "https://openrouter.ai/api/v1"),
        exa_api_key=key("EXA_API_KEY", required=require_exa),
    )
