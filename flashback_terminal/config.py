"""Configuration loader for flashback-terminal."""

import copy
import os
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

PACKAGE_DIR = Path(__file__).parent
USER_CONFIG_DIR = Path.home() / ".config" / "flashback-terminal"
USER_CONFIG_PATH = USER_CONFIG_DIR / "config.yaml"
DATA_DIR = Path.home() / ".local" / "share" / "flashback-terminal"

DEFAULT_CONFIG: Dict[str, Any] = {
    "data_dir": str(DATA_DIR),
    "logging": {
        "verbosity": 2,  # 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG, 4=TRACE
        "log_to_file": False,
        "log_file": None,  # None = auto-generate in data_dir
    },
    "server": {
        "host": "127.0.0.1",
        "port": 9090,
        "ws_ping_interval": 20,
        "ws_ping_timeout": 10,
    },
    "terminal": {
        "rows": 24,
        "cols": 80,
        "shell": None,
        "env": {},
        "login_shell": True,
    },
    "session_manager": {
        "mode": "tmux",  # "screen", "tmux" (REQUIRED: local PTY mode removed)
        "disable_client_capture": True,  # Disable frontend terminal capture
        "screen": {
            "socket_dir": "~/.flashback-terminal/screen",
            "binary": "screen",
            "config_file": None,  # Path to custom screenrc (escape '' for kiosk mode)
            "init_commands": [
                "unset STY",
                "unset SCREENDIR",
                "clear",
            ],
        },
        "tmux": {
            "socket_dir": "~/.flashback-terminal/tmux",
            "binary": "tmux",
            "config_file": None,  # Path to custom tmux.conf
            "nested_session_env": {
                "TMUX": "",
                "TMUX_PANE": "",
                "TMUX_WINDOW": "",
                "TMUX_SESSION": "",
            },
            "init_commands": [
                "unset TMUX",
                "unset TMUX_TMPDIR",
                "unset TMUX_PANE",
                "unset TMUX_WINDOW",
                "unset TMUX_SESSION",
                "clear",
            ],
        },
        "capture": {
            "enabled": True,
            "interval_seconds": 10,
            "max_captures_per_session": 1000,
            "capture_full_scrollback": True,
        },
    },
    "workers": {
        "embedding": {
            "enabled": False,
            "work_interval_seconds": 1,
            "batch_size": 10,
            "mode": "text-only",
            "text": {
                "base_url": "",
                "api_key": "",
                "model": "",
                "dimension": None,
                "extra_headers": {},
            },
            "chunk_size": 512,
            "chunk_overlap": 50,
        },
        "retention": {
            "enabled": True,
            "check_interval_seconds": 3600,
            "strategy": "archive",
            "history_days": 10,
            "archive": {
                "total_size_limit": 10737418240,
                "max_age_days": 90,
                "compression": "gzip",
                "organization": "monthly",
            },
        },
        "cwd_tracker": {
            "enabled": True,
            "method": "pwd_injection",
            "injection_interval_ms": 5000,
        },
    },
    "modules": {
        "history_keeper": {
            "enabled": True,
            "buffer_size_lines": 100,
            "use_fts": True,
        },
        "screenshot_capture": {
            "enabled": True,
            "max_per_session": 1000,
            "max_file_size_mb": 5,
            "formats": ["png", "jpeg"],
        },
        "semantic_search": {
            "enabled": False,
            "text_weight": 0.7,
            "bm25_weight": 0.3,
            "rrf_k": 60,
        },
        "session_recovery": {
            "enabled": True,
            "max_recovery_age_days": 30,
            "replay_mode": "instant",
        },
    },
    "search": {
        "enabled_methods": {"bm25": True, "embedding": False, "regex": True},
        "bm25": {"k1": 1.5, "b": 0.75, "default_limit": 50, "rebuild_interval_seconds": 10},
        "embedding": {"default_limit": 50},
        "context_lines": 3,
    },
    "webui": {
        "enabled": True,
        "theme": "dark",
        "font_family": "'Courier New', monospace",
        "font_size": 14,
    },
    "profiles": [
        {
            "name": "default",
            "shell": None,
            "args": [],
            "env": {},
            "cwd": "~",
            "description": "Standard shell",
        },
    ],
    "validation": {
        "check_embedding_dimension": True,
        "halt_on_embedding_error": False,
    },
}


class Config:
    """Configuration manager for flashback-terminal."""

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path
        self._config = self._load_config()
        self._validate()

    def _load_config(self) -> Dict[str, Any]:
        config = copy.deepcopy(DEFAULT_CONFIG)

        if not HAS_YAML:
            print("[WARN] PyYAML not installed, using default config")
            return config

        config_path = self._config_path
        if config_path is None:
            if USER_CONFIG_PATH.exists():
                config_path = USER_CONFIG_PATH

        if config_path and config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f)
                if user_config:
                    self._deep_merge(config, user_config)

        return config

    def _deep_merge(self, base: Dict, override: Dict) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            elif key in base and isinstance(base[key], list) and isinstance(value, list):
                base[key] = value
            else:
                base[key] = value

    def _validate(self) -> None:
        data_dir = Path(self._config["data_dir"]).expanduser()
        self._config["data_dir"] = str(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        self.log_dir = data_dir / "logs"
        self.screenshot_dir = data_dir / "screenshots"
        self.embedding_dir = data_dir / "embeddings"
        self.archive_dir = data_dir / "archive"
        self.search_index_dir = data_dir / "search_indices"
        self.db_path = data_dir / "terminal.db"

        for d in [self.log_dir, self.screenshot_dir, self.embedding_dir, self.archive_dir, self.search_index_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k, default)
            else:
                return default
            if value is None:
                return default
        return value
    
    def set(self, key:str, value: Any) -> None:
        keys = key.split(".")
        config = self._config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value

    def is_module_enabled(self, module_name: str) -> bool:
        return self.get(f"modules.{module_name}.enabled", True)

    def is_worker_enabled(self, worker_name: str) -> bool:
        return self.get(f"workers.{worker_name}.enabled", True)

    def is_search_enabled(self, method: str) -> bool:
        return self.get(f"search.enabled_methods.{method}", True)

    def get_profile(self, name: str) -> Optional[Dict]:
        profiles = self.get("profiles", [])
        for p in profiles:
            if p.get("name") == name:
                return p
        return profiles[0] if profiles else None

    @property
    def server_host(self) -> str:
        return self.get("server.host", "127.0.0.1")

    @property
    def data_dir(self) -> str:
        return self.get("data_dir")

    @property
    def server_port(self) -> int:
        return self.get("server.port", 8080)

    @property
    def retention_days(self) -> int:
        return self.get("workers.retention.history_days", 10)

    @property
    def verbosity(self) -> int:
        return self.get("logging.verbosity", 2)

    @property
    def session_manager_mode(self) -> str:
        return self.get("session_manager.mode", "tmux")

    def get_session_manager_config(self) -> Dict[str, Any]:
        mode = self.session_manager_mode
        base_config = self.get("session_manager", {})
        mode_config = self.get(f"session_manager.{mode}", {})
        capture_config = self.get("session_manager.capture", {})
        return {
            "mode": mode,
            "mode_config": mode_config,
            "capture": capture_config,
            **base_config,
        }


_config_instance: Optional[Config] = None


def get_config(config_path: Optional[Path] = None) -> Config:
    global _config_instance
    if _config_instance is None or config_path is not None:
        _config_instance = Config(config_path)
    return _config_instance
