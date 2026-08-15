"""Application configuration stored in AppData."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "AgentChat"
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_OUTPUT_FOLDER = Path.home() / "Documents" / "AgentChat" / "outputs"


@dataclass
class AppConfig:
    xai_api_key: str = ""
    openrouter_api_key: str = ""
    output_folder: str = ""
    default_provider: str = "xAI"
    default_model: str = ""
    default_effort: str = "medium"
    disabled_models: dict = field(default_factory=lambda: {"xAI": [], "OpenRouter": []})

    def disabled_for(self, provider: str) -> list[str]:
        raw = self.disabled_models or {}
        values = raw.get(provider, [])
        return list(values) if isinstance(values, list) else []

    def get_output_folder(self) -> Path:
        folder = Path(self.output_folder) if self.output_folder else DEFAULT_OUTPUT_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        return folder


def load_config() -> AppConfig:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        return AppConfig()
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return AppConfig()


def save_config(config: AppConfig) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def merge_config(existing: AppConfig, updates: dict) -> AppConfig:
    data = asdict(existing)
    for key, value in updates.items():
        if key not in data:
            continue
        if key.endswith("_api_key") and value == "":
            continue
        data[key] = value
    return AppConfig(**data)
