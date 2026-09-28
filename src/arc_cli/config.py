from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

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

CONFIG_INSPECTION_SCHEMA = "arc.config-inspection/v1"


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    severity: str
    code: str
    key: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConfigLoadResult:
    path: Path
    exists: bool
    data: dict[str, Any]
    issues: tuple[ConfigIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "valid": self.valid,
            "issues": [issue.as_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class ConfigLayer:
    source: str
    key: str
    value: Any
    selected: bool
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"source": self.source, "key": self.key, "value": self.value, "selected": self.selected}
        if self.detail:
            result["detail"] = self.detail
        return result


@dataclass(frozen=True, slots=True)
class ResolvedConfigValue:
    key: str
    value: Any
    source: str
    layers: tuple[ConfigLayer, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "layers": [layer.as_dict() for layer in self.layers],
        }


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        return Path(root) / "arc" / "config.toml"
    return Path.home() / ".config" / "arc" / "config.toml"


# Retained for callers from early Arc revisions; new code should use config_path().
_config_path = config_path


def _issue(severity: str, code: str, key: str, message: str) -> ConfigIssue:
    return ConfigIssue(severity, code, key, message)


def _validate_table(data: Mapping[str, Any], key: str, issues: list[ConfigIssue]) -> Mapping[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        issues.append(_issue("error", "invalid_type", key, f"[{key}] must be a table"))
        return {}
    return value


def validate_config(data: Mapping[str, Any]) -> tuple[ConfigIssue, ...]:
    issues: list[ConfigIssue] = []
    allowed_top = {"backends", "completion", "create", "profiles", "remote", "remotes", "ui"}
    for key in sorted(set(data) - allowed_top):
        issues.append(_issue("warning", "unknown_key", key, f"unknown top-level configuration key {key!r}"))

    ui = _validate_table(data, "ui", issues)
    for key in sorted(set(ui) - {"progress", "show_native", "native_command_style"}):
        issues.append(_issue("warning", "unknown_key", f"ui.{key}", f"unknown UI configuration key ui.{key}"))
    if "progress" in ui and ui["progress"] not in {"auto", "always", "never"}:
        issues.append(_issue("error", "invalid_value", "ui.progress", "ui.progress must be auto, always, or never"))
    if "show_native" in ui and ui["show_native"] not in {"before", "after", "both"}:
        issues.append(_issue("error", "invalid_value", "ui.show_native", "ui.show_native must be before, after, or both"))
    if "native_command_style" in ui and ui["native_command_style"] not in {"exact", "reproducible"}:
        issues.append(_issue("error", "invalid_value", "ui.native_command_style", "ui.native_command_style must be exact or reproducible"))

    create = _validate_table(data, "create", issues)
    for key in sorted(set(create) - {"level", "threads"}):
        issues.append(_issue("warning", "unknown_key", f"create.{key}", f"unknown create configuration key create.{key}"))
    if "level" in create:
        try:
            level = int(create["level"])
        except (TypeError, ValueError):
            issues.append(_issue("error", "invalid_type", "create.level", "create.level must be an integer from 0 to 9"))
        else:
            if not 0 <= level <= 9:
                issues.append(_issue("error", "invalid_value", "create.level", "create.level must be between 0 and 9"))
    if "threads" in create:
        try:
            threads = int(create["threads"])
        except (TypeError, ValueError):
            issues.append(_issue("error", "invalid_type", "create.threads", "create.threads must be an integer"))
        else:
            if threads < 0:
                issues.append(_issue("error", "invalid_value", "create.threads", "create.threads must be zero or greater"))

    remote = _validate_table(data, "remote", issues)
    for key in sorted(set(remote) - {"execution"}):
        issues.append(_issue("warning", "unknown_key", f"remote.{key}", f"unknown remote configuration key remote.{key}"))
    if "execution" in remote and remote["execution"] not in {"auto", "local", "remote"}:
        issues.append(_issue("error", "invalid_value", "remote.execution", "remote.execution must be auto, local, or remote"))

    backends = _validate_table(data, "backends", issues)
    for key, value in sorted(backends.items()):
        dotted = f"backends.{key}"
        if key not in DEFAULT_BACKENDS:
            issues.append(_issue("warning", "unknown_key", dotted, f"unknown backend preference role {key!r}"))
            continue
        if isinstance(value, str):
            continue
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
            issues.append(_issue("error", "invalid_type", dotted, f"{dotted} must be a non-empty string or list of strings"))

    completion = _validate_table(data, "completion", issues)
    for key in sorted(set(completion) - {"remote_ttl_seconds", "capability_ttl_seconds"}):
        issues.append(_issue("warning", "unknown_key", f"completion.{key}", f"unknown completion configuration key completion.{key}"))
    for key in ("remote_ttl_seconds", "capability_ttl_seconds"):
        if key in completion:
            try:
                value = int(completion[key])
            except (TypeError, ValueError):
                issues.append(_issue("error", "invalid_type", f"completion.{key}", f"completion.{key} must be an integer"))
            else:
                if value < 0:
                    issues.append(_issue("error", "invalid_value", f"completion.{key}", f"completion.{key} must be zero or greater"))

    profiles = _validate_table(data, "profiles", issues)
    allowed_profile = {
        "backend", "progress", "level", "threads", "yazi", "show_native", "native_style", "execution",
        "exclude", "include", "exclude_from", "include_from",
    }
    for name, value in sorted(profiles.items()):
        prefix = f"profiles.{name}"
        if not isinstance(value, dict):
            issues.append(_issue("error", "invalid_type", prefix, f"profile {name!r} must be a table"))
            continue
        for key in sorted(set(value) - allowed_profile):
            issues.append(_issue("warning", "unknown_key", f"{prefix}.{key}", f"profile {name!r} contains unsupported option {key!r}"))
        if "backend" in value and not isinstance(value["backend"], str):
            issues.append(_issue("error", "invalid_type", f"{prefix}.backend", f"profile {name!r} backend must be a string"))
        if "progress" in value and value["progress"] not in {"auto", "always", "never"}:
            issues.append(_issue("error", "invalid_value", f"{prefix}.progress", f"profile {name!r} progress must be auto, always, or never"))
        if "level" in value:
            try:
                level = int(value["level"])
            except (TypeError, ValueError):
                issues.append(_issue("error", "invalid_type", f"{prefix}.level", f"profile {name!r} level must be an integer from 0 to 9"))
            else:
                if not 0 <= level <= 9:
                    issues.append(_issue("error", "invalid_value", f"{prefix}.level", f"profile {name!r} level must be between 0 and 9"))
        if "threads" in value:
            try:
                threads = int(value["threads"])
            except (TypeError, ValueError):
                issues.append(_issue("error", "invalid_type", f"{prefix}.threads", f"profile {name!r} threads must be an integer"))
            else:
                if threads < 0:
                    issues.append(_issue("error", "invalid_value", f"{prefix}.threads", f"profile {name!r} threads must be zero or greater"))
        for option, allowed in (("yazi", {"auto", "archive", "inputs", "output"}), ("show_native", {"before", "after", "both"}), ("native_style", {"exact", "reproducible"}), ("execution", {"auto", "local", "remote"})):
            if option in value and value[option] not in allowed:
                issues.append(_issue("error", "invalid_value", f"{prefix}.{option}", f"profile {name!r} {option} is invalid"))
        for option in ("exclude", "include", "exclude_from", "include_from"):
            if option not in value:
                continue
            raw = value[option]
            values = raw if isinstance(raw, list) else [raw]
            if not all(isinstance(item, str) for item in values):
                issues.append(_issue("error", "invalid_type", f"{prefix}.{option}", f"profile {name!r} {option} must be a string or list of strings"))

    remotes = _validate_table(data, "remotes", issues)
    allowed_remote = {
        "type", "host", "user", "port", "identity_file", "proxy_jump", "ssh_args", "remote",
        "completion_ttl_seconds", "capability_ttl_seconds",
    }
    for name, value in sorted(remotes.items()):
        prefix = f"remotes.{name}"
        if not isinstance(value, dict):
            issues.append(_issue("error", "invalid_type", prefix, f"remote {name!r} must be a table"))
            continue
        for key in sorted(set(value) - allowed_remote):
            issues.append(_issue("warning", "unknown_key", f"{prefix}.{key}", f"remote {name!r} contains unknown option {key!r}"))
        kind = value.get("type")
        if kind not in {"ssh", "rclone"}:
            issues.append(_issue("error", "invalid_value", f"{prefix}.type", f"remote {name!r} type must be ssh or rclone"))
        if "port" in value:
            try:
                port = int(value["port"])
            except (TypeError, ValueError):
                issues.append(_issue("error", "invalid_type", f"{prefix}.port", f"remote {name!r} port must be an integer"))
            else:
                if not 1 <= port <= 65535:
                    issues.append(_issue("error", "invalid_value", f"{prefix}.port", f"remote {name!r} port must be between 1 and 65535"))
        if "ssh_args" in value and (not isinstance(value["ssh_args"], list) or not all(isinstance(item, str) for item in value["ssh_args"])):
            issues.append(_issue("error", "invalid_type", f"{prefix}.ssh_args", f"remote {name!r} ssh_args must be a list of strings"))
        for ttl_key in ("completion_ttl_seconds", "capability_ttl_seconds"):
            if ttl_key in value:
                try:
                    ttl = int(value[ttl_key])
                except (TypeError, ValueError):
                    issues.append(_issue("error", "invalid_type", f"{prefix}.{ttl_key}", f"remote {name!r} {ttl_key} must be an integer"))
                else:
                    if ttl < 0:
                        issues.append(_issue("error", "invalid_value", f"{prefix}.{ttl_key}", f"remote {name!r} {ttl_key} must be zero or greater"))

    return tuple(issues)


def load_config_result() -> ConfigLoadResult:
    path = config_path()
    if not path.is_file():
        return ConfigLoadResult(path, False, {}, ())
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        return ConfigLoadResult(path, True, {}, (_issue("error", "toml_decode", "", f"invalid TOML in {path}: {exc}"),))
    except OSError as exc:
        return ConfigLoadResult(path, True, {}, (_issue("error", "read_error", "", f"cannot read {path}: {exc}"),))
    if not isinstance(raw, dict):
        return ConfigLoadResult(path, True, {}, (_issue("error", "invalid_root", "", f"configuration root in {path} must be a table"),))
    data = dict(raw)
    return ConfigLoadResult(path, True, data, validate_config(data))


def load_config(*, strict: bool = False) -> dict:
    result = load_config_result()
    if strict and not result.valid:
        message = "; ".join(issue.message for issue in result.issues if issue.severity == "error")
        raise UsageError(message or f"invalid configuration: {result.path}")
    return result.data


def backend_preferences(config: dict, key: str) -> list[str]:
    # Keep runtime backend ordering on the same provenance resolver surfaced by
    # `arc config explain backends.ROLE`.
    return list(resolve_config_value(f"backends.{key}", config).value)


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


def _parse_int(value: Any, key: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"{key} must be an integer") from exc


def _normalize_enum(value: Any, key: str, allowed: set[str]) -> str:
    text = str(value)
    if text not in allowed:
        raise UsageError(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return text


def _normalize_level(value: Any, key: str) -> int:
    level = _parse_int(value, key)
    if not 0 <= level <= 9:
        raise UsageError(f"{key} must be between 0 and 9")
    return level


def _normalize_threads(value: Any, key: str) -> int:
    threads = _parse_int(value, key)
    if threads < 0:
        raise UsageError(f"{key} must be zero or greater")
    return threads


@dataclass(frozen=True, slots=True)
class _SettingSpec:
    key: str
    default: Any
    config_path: tuple[str, ...]
    env: str | None
    profile_key: str | None
    cli_key: str | None
    normalize: Callable[[Any, str], Any]


def _identity(value: Any, _key: str) -> Any:
    return value


SETTING_SPECS: dict[str, _SettingSpec] = {
    "ui.progress": _SettingSpec("ui.progress", "auto", ("ui", "progress"), "ARC_PROGRESS", "progress", "progress", lambda v, k: _normalize_enum(v, k, {"auto", "always", "never"})),
    "create.level": _SettingSpec("create.level", None, ("create", "level"), "ARC_LEVEL", "level", "level", _normalize_level),
    "create.threads": _SettingSpec("create.threads", None, ("create", "threads"), "ARC_THREADS", "threads", "threads", _normalize_threads),
    "ui.show_native": _SettingSpec("ui.show_native", None, ("ui", "show_native"), None, "show_native", "show_native", lambda v, k: _normalize_enum(v, k, {"before", "after", "both"})),
    "ui.native_command_style": _SettingSpec("ui.native_command_style", "reproducible", ("ui", "native_command_style"), None, "native_style", "native_style", lambda v, k: _normalize_enum(v, k, {"exact", "reproducible"})),
    "remote.execution": _SettingSpec("remote.execution", "auto", ("remote", "execution"), None, "execution", "execution", lambda v, k: _normalize_enum(v, k, {"auto", "local", "remote"})),
    "command.backend": _SettingSpec("command.backend", None, (), None, "backend", "backend", _identity),
}


def configuration_keys() -> tuple[str, ...]:
    backend_keys = tuple(f"backends.{name}" for name in sorted(DEFAULT_BACKENDS))
    return tuple(SETTING_SPECS) + backend_keys


def _nested_get(data: Mapping[str, Any], path: tuple[str, ...]) -> tuple[bool, Any]:
    if not path:
        return False, None
    current: Any = data
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _cli_values(cli: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return cli or {}


def resolve_config_value(
    key: str,
    config: Mapping[str, Any],
    *,
    profile: str | None = None,
    cli: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedConfigValue:
    env = os.environ if environ is None else environ
    cli_values = _cli_values(cli)
    layers: list[ConfigLayer] = []

    if key.startswith("backends."):
        role = key.split(".", 1)[1]
        if role not in DEFAULT_BACKENDS:
            raise UsageError(f"unknown configuration key: {key}")
        candidates: list[tuple[str, Any, str | None]] = [("builtin", list(DEFAULT_BACKENDS[role]), "compiled default")]
        exists, value = _nested_get(config, ("backends", role))
        if exists:
            normalized = [value] if isinstance(value, str) else list(value)
            candidates.append(("config", normalized, str(config_path())))
        env_key = "ARC_BACKEND_" + role.upper().replace("-", "_")
        if env.get(env_key):
            normalized = [x.strip() for x in str(env[env_key]).replace(os.pathsep, ",").split(",") if x.strip()]
            candidates.append(("environment", normalized, env_key))
        selected = len(candidates) - 1
        for index, (source, value, detail) in enumerate(candidates):
            layers.append(ConfigLayer(source, key, value, index == selected, detail))
        source, value, _ = candidates[selected]
        return ResolvedConfigValue(key, value, source, tuple(layers))

    spec = SETTING_SPECS.get(key)
    if spec is None:
        raise UsageError(f"unknown configuration key: {key}")
    candidates: list[tuple[str, Any, str | None]] = [("builtin", spec.default, "compiled default")]
    exists, value = _nested_get(config, spec.config_path)
    if exists:
        candidates.append(("config", spec.normalize(value, key), str(config_path())))
    if spec.env and spec.env in env and env[spec.env] != "":
        candidates.append(("environment", spec.normalize(env[spec.env], key), spec.env))
    if profile and spec.profile_key:
        profile_data = get_profile(dict(config), profile)
        if spec.profile_key in profile_data:
            candidates.append(("profile", spec.normalize(profile_data[spec.profile_key], key), profile))
    if spec.cli_key and spec.cli_key in cli_values and cli_values[spec.cli_key] is not None:
        candidates.append(("cli", spec.normalize(cli_values[spec.cli_key], key), f"--{spec.cli_key.replace('_', '-') }"))
    selected = len(candidates) - 1
    for index, (source, value, detail) in enumerate(candidates):
        layers.append(ConfigLayer(source, key, value, index == selected, detail))
    source, value, _ = candidates[selected]
    return ResolvedConfigValue(key, value, source, tuple(layers))


def resolve_effective_config(
    config: Mapping[str, Any],
    *,
    profile: str | None = None,
    cli: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, ResolvedConfigValue]:
    return {
        key: resolve_config_value(key, config, profile=profile, cli=cli, environ=environ)
        for key in configuration_keys()
    }


def parse_cli_overrides(values: list[str] | tuple[str, ...]) -> dict[str, Any]:
    allowed = {
        "backend": "backend",
        "progress": "progress",
        "level": "level",
        "threads": "threads",
        "show_native": "show_native",
        "native_style": "native_style",
        "execution": "execution",
    }
    result: dict[str, Any] = {}
    for item in values:
        name, sep, value = item.partition("=")
        normalized = name.strip().lstrip("-").replace("-", "_")
        if not sep or normalized not in allowed:
            names = ", ".join(sorted(allowed))
            raise UsageError(f"--cli requires OPTION=VALUE; supported options: {names}")
        result[allowed[normalized]] = value
    return result


def config_inspection_payload(
    config_result: ConfigLoadResult,
    *,
    profile: str | None = None,
    cli: Mapping[str, Any] | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": CONFIG_INSPECTION_SCHEMA,
        "config": config_result.as_dict(),
        "profile": profile,
        "cli_overrides": dict(cli or {}),
    }
    if key is not None:
        payload["resolution"] = resolve_config_value(key, config_result.data, profile=profile, cli=cli).as_dict()
    else:
        payload["effective"] = {
            name: value.as_dict()
            for name, value in resolve_effective_config(config_result.data, profile=profile, cli=cli).items()
        }
    if profile:
        payload["resolved_profile"] = get_profile(config_result.data, profile)
    return payload
