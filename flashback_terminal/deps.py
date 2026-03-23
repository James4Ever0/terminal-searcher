"""Dependency checker for flashback-terminal."""

import shutil
import sys
from typing import Optional

from flashback_terminal.config import get_config


class DependencyError(Exception):
    """Error raised when a dependency is missing."""

    def __init__(self, message: str, install_cmd: str):
        self.message = message
        self.install_cmd = install_cmd
        super().__init__(message)

    def __str__(self) -> str:
        return f"""
{'='*70}
DEPENDENCY ERROR: {self.message}
{'='*70}

To fix this issue, run:

    {self.install_cmd}

{'='*70}
"""


class DependencyChecker:
    """Checks for system and Python dependencies."""

    @classmethod
    def check_all(cls, skip_optional: bool = False) -> bool:
        """Check all dependencies."""
        errors = []
        config = get_config()

        shell = config.get("terminal.shell") or shutil.which("bash") or shutil.which("sh")
        if not shell:
            errors.append(
                DependencyError("No shell found (bash or sh required)", "sudo apt-get install bash")
            )

        if config.is_worker_enabled("embedding") and not skip_optional:
            try:
                import requests  # noqa: F401
            except ImportError:
                errors.append(
                    DependencyError(
                        "requests required for embedding worker",
                        "pip install requests",
                    )
                )

        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            sys.exit(1)

        return True


def check_python_module(module_name: str, pip_package: Optional[str] = None) -> bool:
    """Check if a Python module is installed."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        pkg = pip_package or module_name
        print(f"[ERROR] Python module '{module_name}' not found")
        print(f"[INFO] Install with: pip install {pkg}")
        return False
