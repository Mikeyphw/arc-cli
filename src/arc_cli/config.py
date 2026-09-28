from __future__ import annotations

import os
import tomllib
from pathlib import Path

from .errors import UsageError

DEFAULT_BACKENDS = {
    "tar": ["bsdtar", "tar"],
    "zip_extract": ["7zz", "7z", "unzip"],
    "zip_create": ["7zz", "7z", "zip"],
    "7z": ["7zz", "7z"],
    "rar_extract": ["7zz", "7z", "unrar"],
    "rar_create": ["rar"],
    "gzip": ["pigz", "gzip"],
    "bzip2": ["pbzip2", "bzip2"],
    "xz": ["pixz", "xz"],
    "zstd": ["pzstd", "zstd"],
}


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / "arc" / "config.toml"
    return Path.home() / ".config" / "arc" / "config.toml"


# Retained for callers from early Arc revisions; new code should use config_path().
_config_path = config_path


def load_config() -> dict:
    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def backend_preferences(config: dict, key: str) -> list[str]:
    env_key = "ARC_BACKEND_" + key.upper().replace("-", "_")
    env_value = os.environ.get(env_key)
    if env_value:
        return [x.strip() for x in env_value.replace(os.pathsep, ",").split(",") if x.strip()]
    configured = config.get("backends", {}).get(key)
    if isinstance(configured, str):
        return [configured]
    if isinstance(configured, list) and all(isinstance(x, str) for x in configured):
        return configured
    return list(DEFAULT_BACKENDS[key])


def profile_names(config: dict) -> list[str]:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict):
        return []
    return sorted(str(name) for name, value in profiles.items() if isinstance(value, dict))


def get_profile(config: dict, name: str) -> dict:
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or not isinstance(profiles.get(name), dict):
        available = ", ".join(profile_names(config)) or "none configured"
        raise UsageError(f"unknown profile {name!r}; available profiles: {available}")
    return dict(profiles[name])
