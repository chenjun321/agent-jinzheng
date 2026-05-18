from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


@lru_cache
def load_runtime_config(config_path: str = "config/default.yaml") -> dict[str, Any]:
    default_path = Path("config/default.yaml")
    data: dict[str, Any] = {}
    if default_path.exists():
        data = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}

    selected_path = Path(config_path)
    if selected_path.exists() and selected_path.resolve() != default_path.resolve():
        selected = yaml.safe_load(selected_path.read_text(encoding="utf-8")) or {}
        data = deep_merge(data, selected)
    return data


def config_get(config: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current

