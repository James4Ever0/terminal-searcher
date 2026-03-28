"""Dependency checker for flashback-terminal."""

import shutil
import sys
from typing import List, Optional

from flashback_terminal.config import get_config


class DependencyError(Exception):
    """Error raised when a dependency is missing."""

    def __init__(self, message: str, install_cmd: str):
        self.message = message
        self.install_cmd = install_cmd
        super().__init__(message)

    def __str__(self) -> str:
        install_cmd_indented='\n'.join(['    '+it for it in self.install_cmd.splitlines()])
        return f"""
{'='*70}
DEPENDENCY ERROR: {self.message}
{'='*70}

To fix this issue, run:

{install_cmd_indented}

{'='*70}
"""




class BinaryDependencyError(DependencyError):
    """Error raised when a required binary is missing."""

    def __init__(self, binary: str, purpose: str, install_cmd: str):
        self.binary = binary
        self.purpose = purpose
        super().__init__(
            f"Binary '{binary}' not found in PATH (required for {purpose})",
            install_cmd
        )


class DependencyChecker:
    """Checks for system and Python dependencies."""

    @classmethod
    def check_all(cls, skip_optional: bool = False) -> bool:
        """Check all dependencies."""
        errors = []
        config = get_config()

        # Check for shell
        shell = config.get("terminal.shell") or shutil.which("bash") or shutil.which("sh")
        if not shell:
            errors.append(
                DependencyError("No shell found (bash or sh required)", "sudo apt-get install bash")
            )

        # Check session manager specific dependencies
        session_errors = cls.check_session_manager_deps(config)
        errors.extend(session_errors)

        # Check embedding worker dependencies
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

    @classmethod
    def check_session_manager_deps(cls, config) -> List[DependencyError]:
        """Check dependencies for configured session manager."""
        errors = []
        mode = config.session_manager_mode

        if mode == "tmux":
            binary = config.get("session_manager.tmux.binary", "tmux")
            if not shutil.which(binary):
                errors.append(BinaryDependencyError(
                    binary=binary,
                    purpose="tmux session management",
                    install_cmd=(
                        "sudo apt-get install tmux  # Debian/Ubuntu\n"
                        "sudo yum install tmux      # RHEL/CentOS\n"
                        "brew install tmux          # macOS\n"
                        "pacman -S tmux             # Arch Linux"
                    )
                ))

        elif mode == "screen":
            binary = config.get("session_manager.screen.binary", "screen")
            if not shutil.which(binary):
                errors.append(BinaryDependencyError(
                    binary=binary,
                    purpose="screen session management",
                    install_cmd=(
                        "sudo apt-get install screen  # Debian/Ubuntu\n"
                        "sudo yum install screen      # RHEL/CentOS\n"
                        "brew install screen          # macOS\n"
                        "pacman -S screen             # Arch Linux"
                    )
                ))

        return errors

    @classmethod
    def check_binary(cls, binary: str, purpose: str = "") -> Optional[str]:
        """Check if a binary exists in PATH and return its path."""
        path = shutil.which(binary)
        if path:
            return path
        return None

    @classmethod
    def print_session_manager_info(cls) -> None:
        """Print information about session manager configuration."""
        config = get_config()
        mode = config.session_manager_mode

        print("Session Manager Configuration:")
        print(f"  Mode: {mode}")

        if mode == "local":
            print("  Using: Direct PTY fork (no external dependencies)")
        elif mode == "tmux":
            binary = config.get("session_manager.tmux.binary", "tmux")
            socket_dir = config.get("session_manager.tmux.socket_dir", "~/.flashback-terminal/tmux")
            binary_path = cls.check_binary(binary)
            status = f"✓ {binary_path}" if binary_path else f"✗ {binary} not found"
            print(f"  Binary: {status}")
            print(f"  Socket directory: {socket_dir}")
        elif mode == "screen":
            binary = config.get("session_manager.screen.binary", "screen")
            socket_dir = config.get("session_manager.screen.socket_dir", "~/.flashback-terminal/screen")
            binary_path = cls.check_binary(binary)
            status = f"✓ {binary_path}" if binary_path else f"✗ {binary} not found"
            print(f"  Binary: {status}")
            print(f"  Socket directory: {socket_dir}")


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
