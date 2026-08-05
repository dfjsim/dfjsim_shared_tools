"""Reusable TOML configuration loader for dfjsim applications."""

import logging
import multiprocessing
import pprint
import sys
import tomllib
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

CONFIG_DEFAULT_FILENAME = Path("config_default.toml")
CONFIG_FILENAME = Path("config.toml")
CONFIGS_DEFAULT_FOLDER = Path("configs_default")
CONFIGS_FOLDER = Path("configs")

LOGGING_LEVELS = {
    0: logging.CRITICAL,
    1: logging.ERROR,
    2: logging.WARNING,
    3: logging.INFO,
    4: logging.DEBUG,
}

LOGGING_FORMAT = "%(asctime)s - %(levelname)s - %(funcName)s() - %(message)s"


class ConfigLoader:
    def __init__(self) -> None:
        self._merged_config: dict[str, Any] | None = None
        self._logging_configured = False
        self._use_folder_config: bool | None = None
        logging.basicConfig(level=logging.INFO, format=LOGGING_FORMAT)

    def _should_use_folder_config(self) -> bool:
        if self._use_folder_config is None:
            self._use_folder_config = CONFIGS_DEFAULT_FOLDER.exists()
        return self._use_folder_config

    def _print_config(self) -> None:
        if self._merged_config is None:
            raise ValueError("Config files have not been loaded yet. Call get_config() first.")

        if multiprocessing.current_process().name != "MainProcess":
            return

        print("\nLoaded config:\n")
        print("=" * 80)
        pprint.pprint(self._merged_config)
        print("=" * 80)

    def _read_toml_file(self, filename: Path, required: bool = False) -> dict[str, Any]:
        try:
            with filename.open("rb") as handle:
                config = tomllib.load(handle)
            logging.info("Loading config: %s", filename)
            return config
        except FileNotFoundError:
            if required:
                logging.error("Required config file not found: %s", filename)
                raise
            logging.debug("Config file %s not found; using empty config.", filename)
            return {}
        except Exception as exc:
            if required:
                logging.error("Error loading %s: %s", filename, exc)
                raise
            logging.warning("Error loading %s: %s", filename, exc)
            return {}

    def _deep_merge(self, dict1: dict, dict2: dict) -> dict:
        result = dict1.copy()

        for key, value in dict2.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                if result[key] and isinstance(result[key][0], dict) and "type" in result[key][0]:
                    existing_by_type = {item["type"]: item for item in result[key]}
                    for new_item in value:
                        if isinstance(new_item, dict) and "type" in new_item:
                            item_type = new_item["type"]
                            if item_type in existing_by_type:
                                existing_by_type[item_type] = self._deep_merge(existing_by_type[item_type], new_item)
                            else:
                                existing_by_type[item_type] = new_item
                    result[key] = list(existing_by_type.values())
                else:
                    result[key] = result[key] + value
            else:
                result[key] = value

        return result

    def _load_folder_configs(self, folder: Path) -> dict[str, Any]:
        return self._load_folder_configs_limited(folder)

    def _load_folder_configs_limited(
        self,
        folder: Path,
        *,
        allowed_subfolders: set[str] | None = None,
        include_general: bool = True,
    ) -> dict[str, Any]:
        if not folder.exists():
            logging.debug("Config folder not found: %s", folder)
            return {}

        merged_config: dict[str, Any] = {}
        if include_general:
            general_path = folder / "general.toml"
            if general_path.exists():
                general_config = self._read_toml_file(general_path)
                merged_config = self._deep_merge(merged_config, general_config)

        for subfolder in sorted(folder.iterdir()):
            if not subfolder.is_dir():
                continue
            if allowed_subfolders is not None and subfolder.name not in allowed_subfolders:
                continue
            for toml_file in sorted(subfolder.glob("*.toml")):
                sub_config = self._read_toml_file(toml_file)
                merged_config = self._deep_merge(merged_config, sub_config)

        return merged_config

    def load_named_subfolder_configs_fresh(self, subfolder: str) -> dict[str, dict[str, Any]]:
        if not self._should_use_folder_config():
            return {}

        default_dir = CONFIGS_DEFAULT_FOLDER / subfolder
        user_dir = CONFIGS_FOLDER / subfolder
        configs: dict[str, dict[str, Any]] = {}

        def _load_dir(path: Path) -> dict[str, dict[str, Any]]:
            if not path.exists() or not path.is_dir():
                return {}
            out: dict[str, dict[str, Any]] = {}
            for toml_file in sorted(path.glob("*.toml")):
                out[toml_file.stem] = self._read_toml_file(toml_file)
            return out

        defaults = _load_dir(default_dir)
        overrides = _load_dir(user_dir)

        for name, default_cfg in defaults.items():
            configs[name] = default_cfg
        for name, override_cfg in overrides.items():
            configs[name] = self._deep_merge(configs[name], override_cfg) if name in configs else override_cfg

        return configs

    def _load_and_merge_configs(self) -> None:
        if self._merged_config is not None:
            return

        if self._should_use_folder_config():
            default_config = self._load_folder_configs(CONFIGS_DEFAULT_FOLDER)
            user_config = self._load_folder_configs(CONFIGS_FOLDER)
        else:
            default_config = self._read_toml_file(CONFIG_DEFAULT_FILENAME)
            user_config = self._read_toml_file(CONFIG_FILENAME)

        self._merged_config = self._deep_merge(default_config, user_config)

        if self._merged_config.get("logging_level", 3) >= 4:
            self._print_config()

    def get_config(self, config_class: type[T]) -> T:
        self._load_and_merge_configs()
        if not self._logging_configured:
            self.configure_logging()

        assert self._merged_config is not None, "Configuration wasn't loaded successfully."

        config_section = None
        get_section_method = getattr(config_class, "_get_config_section", None)
        if get_section_method is not None and callable(get_section_method):
            config_section = get_section_method()

        if isinstance(config_section, str):
            return config_class(**self._merged_config.get(config_section, {}))
        return config_class(**self._merged_config)

    def get(self, key: str, default: Any = None) -> Any:
        self._load_and_merge_configs()
        return self._merged_config.get(key, default) if self._merged_config else default

    def configure_logging(self) -> None:
        if self._logging_configured:
            return

        if self._merged_config is not None:
            user_logging_level = self._merged_config.get("logging_level", 3)
        else:
            if self._should_use_folder_config():
                default_general = self._read_toml_file(CONFIGS_DEFAULT_FOLDER / "general.toml")
                user_general = self._read_toml_file(CONFIGS_FOLDER / "general.toml")
                merged_general = self._deep_merge(default_general, user_general)
                user_logging_level = merged_general.get("logging_level", 3)
            else:
                default_config = self._read_toml_file(CONFIG_DEFAULT_FILENAME)
                user_config = self._read_toml_file(CONFIG_FILENAME)
                merged = self._deep_merge(default_config, user_config)
                user_logging_level = merged.get("logging_level", 3)

        logging_level = LOGGING_LEVELS.get(user_logging_level, logging.INFO)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging_level)

        if not root_logger.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(LOGGING_FORMAT))
            root_logger.addHandler(handler)
        else:
            for handler in root_logger.handlers:
                handler.setLevel(logging_level)

        self._logging_configured = True
        logging.debug(
            "Logging configured with level: %s (from config level: %s)",
            logging.getLevelName(logging_level),
            user_logging_level,
        )

    def load_section_fresh(self, section: str) -> dict[str, Any]:
        if self._should_use_folder_config():
            allowed = {section}
            default_config = self._load_folder_configs_limited(CONFIGS_DEFAULT_FOLDER, allowed_subfolders=allowed)
            user_config = self._load_folder_configs_limited(CONFIGS_FOLDER, allowed_subfolders=allowed)
        else:
            default_config = self._read_toml_file(CONFIG_DEFAULT_FILENAME)
            user_config = self._read_toml_file(CONFIG_FILENAME)

        merged = self._deep_merge(default_config, user_config)
        return merged.get(section, {})


config_loader = ConfigLoader()
