#!/usr/bin/env python3
"""
Test PTY attachment for tmux/screen sessions.
Run with tmux mode: python test_pty_attachment.py tmux
Run with screen mode: python test_pty_attachment.py screen
"""

import os
import sys
import time
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

def test_pty_attachment(mode: str):
    """Test PTY attachment for given multiplexer mode."""
    print(f"Testing PTY attachment for {mode}...")

    # Create temporary config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        config_content = f"""data_dir: "/tmp/flashback-test-{mode}"
logging:
  verbosity: 2
server:
  host: "127.0.0.1"
  port: 0
session_manager:
  mode: "{mode}"
  disable_client_capture: true
  {mode}:
    socket_dir: "/tmp/flashback-test-{mode}/sockets"
modules:
  history_keeper:
    enabled: false
"""
        f.write(config_content)
        config_path = f.name

    os.environ['FLASHBACK_CONFIG'] = config_path

    try:
        # Import after setting environment variable
        from flashback_terminal.config import get_config
        from flashback_terminal.session_manager import SessionManager

        # Force new config instance by providing config_path
        config = get_config(Path(config_path))

        print(f"Config loaded, mode: {config.session_manager_mode}")

        # Create session manager
        manager = SessionManager()

        # Create a simple profile
        profile = {
            "shell": "/bin/bash",
            "args": [],
            "cwd": os.path.expanduser("~"),
            "env": {},
            "login_shell": True,
        }

        session_id = f"test-session-{int(time.time())}"
        print(f"Creating session {session_id}...")

        session = manager.create_session(
            session_id=session_id,
            name="Test Session",
            profile=profile,
            on_output=lambda data: print(f"Output: {data!r}")
        )

        if not session:
            print("Failed to create session")
            return False

        print(f"Session created: {session.__class__.__name__}")

        # Check if pty_fd is available (access private attribute)
        if hasattr(session, '_pty_fd'):
            pty_fd = session._pty_fd
            if pty_fd is not None:
                print(f"✓ PTY file descriptor opened: {pty_fd}")
            else:
                print("✗ PTY file descriptor is None (pty unavailable)")
        else:
            print("✗ Session does not have _pty_fd attribute")
            return False

        # Test resize via pty
        print("Testing resize...")
        session.resize(25, 80)

        # Write a simple command (echo)
        print("Writing command 'echo hello'...")
        session.write("echo hello\n")

        # Wait a bit for output
        time.sleep(0.5)

        # Try to read output (non-blocking)
        print("Reading output...")
        output = session.read(timeout=0.5)
        if output:
            print(f"Received output: {output!r}")
        else:
            print("No output received (may be normal)")

        # Capture session content
        print("Capturing session content...")
        capture = session.capture(full_scrollback=False)
        if capture and capture.text:
            print(f"Capture text length: {len(capture.text)}")
            if "hello" in capture.text:
                print("✓ Capture contains 'hello'")
            else:
                print("✗ Capture does not contain 'hello'")
        else:
            print("No capture returned")

        # Stop session
        print("Stopping session...")
        session.stop()

        print("✓ PTY attachment test completed")
        return True

    except Exception as e:
        print(f"✗ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Clean up config file
        try:
            os.unlink(config_path)
        except:
            pass

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ('tmux', 'screen'):
        print("Usage: python test_pty_attachment.py [tmux|screen]")
        print("Note: Ensure the multiplexer binary is installed and in PATH")
        sys.exit(1)

    mode = sys.argv[1]
    success = test_pty_attachment(mode)
    sys.exit(0 if success else 1)