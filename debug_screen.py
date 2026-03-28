#!/usr/bin/env python3
"""
Debug screen session startup.
Run: python debug_screen.py
"""

import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

def debug_screen():
    print("Debugging screen session startup...")

    # Create temporary config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_content = """data_dir: "/tmp/flashback-debug-screen"
logging:
  verbosity: 4  # TRACE
server:
  host: "127.0.0.1"
  port: 0
session_manager:
  mode: "screen"
  disable_client_capture: true
  screen:
    socket_dir: "/tmp/flashback-debug-screen/sockets"
modules:
  history_keeper:
    enabled: false
"""
        f.write(config_content)
        config_path = f.name

    os.environ['FLASHBACK_CONFIG'] = config_path

    try:
        from flashback_terminal.config import get_config
        from flashback_terminal.session_manager import SessionManager, SessionManagerError

        # Force new config instance
        config = get_config(Path(config_path))
        print(f"Config loaded, mode: {config.session_manager_mode}")
        print(f"Screen socket dir: {config.get('session_manager.screen.socket_dir')}")

        # Check if screen binary exists
        import shutil
        screen_binary = config.get('session_manager.screen.binary', 'screen')
        screen_path = shutil.which(screen_binary)
        print(f"Screen binary '{screen_binary}': {screen_path}")
        if not screen_path:
            print("Screen not found in PATH")
            return False

        # Create session manager
        print("\nCreating SessionManager...")
        manager = SessionManager()

        # Create a simple profile
        profile = {
            "shell": "/bin/bash",
            "args": [],
            "cwd": os.path.expanduser("~"),
            "env": {},
            "login_shell": True,
        }

        session_id = f"debug-session-{os.getpid()}"
        print(f"\nCreating session {session_id}...")

        # Try to create session with detailed error handling
        try:
            session = manager.create_session(
                session_id=session_id,
                name="Debug Session",
                profile=profile,
                on_output=lambda data: print(f"Output: {data!r}")
            )

            if session:
                print(f"Session created: {session.__class__.__name__}")
                print(f"Session name: {session._session_name}")
                print(f"Socket dir: {session._socket_dir}")

                # Check pty
                if hasattr(session, '_pty_fd'):
                    print(f"PTY fd: {session._pty_fd}")

                # Stop session
                session.stop()
                print("Session stopped")
                return True
            else:
                print("Failed to create session (returned None)")
                return False

        except SessionManagerError as e:
            print(f"\nSessionManagerError: {e}")
            # Try to run screen command manually to see error
            import subprocess
            socket_dir = "/tmp/flashback-debug-screen/sockets"
            session_name = f"flashback-{session_id}"
            cmd = ["screen", "-S", session_name, "-d", "-m", "-s", "/bin/bash", "bash", "-c", "echo test"]
            env = os.environ.copy()
            env["SCREENDIR"] = socket_dir
            env.pop("STY", None)
            print(f"\nTrying manual screen command:")
            print(f"  Command: {' '.join(cmd)}")
            print(f"  SCREENDIR={socket_dir}")
            result = subprocess.run(cmd, capture_output=True, text=True, env=env)
            print(f"  Return code: {result.returncode}")
            print(f"  Stdout: {result.stdout}")
            print(f"  Stderr: {result.stderr}")
            return False

    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            os.unlink(config_path)
        except:
            pass

if __name__ == "__main__":
    success = debug_screen()
    sys.exit(0 if success else 1)