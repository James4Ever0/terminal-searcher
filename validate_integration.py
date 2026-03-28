#!/usr/bin/env python3
"""Validate flashback-terminal integration after implementing todo.txt tasks.

Checks:
1. Configuration loading and session manager mode
2. Database schema for terminal_captures table
3. Session manager dependencies (tmux/screen binaries)
4. Capture worker imports (agg_python_bindings optional)
5. Timeline API endpoint definitions
6. Template files existence
"""

import os
import sys
import importlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent

def check_imports():
    """Check that all required modules can be imported."""
    modules = [
        'flashback_terminal.config',
        'flashback_terminal.database',
        'flashback_terminal.session_manager',
        'flashback_terminal.terminal',
        'flashback_terminal.server',
        'flashback_terminal.workers.capture_worker',
    ]

    for module in modules:
        try:
            importlib.import_module(module)
            print(f"✓ {module}")
        except ImportError as e:
            print(f"✗ {module}: {e}")
            return False
    return True

def check_config():
    """Check configuration loads correctly."""
    try:
        from flashback_terminal.config import get_config
        config = get_config()

        # Check session manager mode
        mode = config.session_manager_mode
        print(f"✓ Config loaded, session manager mode: {mode}")

        if mode not in ('screen', 'tmux'):
            print(f"✗ Invalid session manager mode: {mode} (expected 'screen' or 'tmux')")
            return False

        # Check client capture disabled
        disabled = config.get("session_manager.disable_client_capture", True)
        if not disabled:
            print("⚠ Client capture not disabled (should be True for screen/tmux only mode)")

        return True
    except Exception as e:
        print(f"✗ Config check failed: {e}")
        return False

def check_database_schema():
    """Check database schema includes terminal_captures table."""
    try:
        from flashback_terminal.database import Database
        from flashback_terminal.config import get_config

        config = get_config()
        db_path = config.db_path

        # Initialize database (will create tables if not exist)
        db = Database(db_path)

        # Check if terminal_captures table exists
        with db._connect() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='terminal_captures'"
            ).fetchone()

            if tables:
                print("✓ terminal_captures table exists")

                # Check columns
                cols = conn.execute("PRAGMA table_info(terminal_captures)").fetchall()
                col_names = [c[1] for c in cols]
                required = ['session_id', 'screenshot_path', 'text_content', 'ansi_content', 'capture_type']
                missing = [c for c in required if c not in col_names]

                if missing:
                    print(f"✗ Missing columns in terminal_captures: {missing}")
                    return False
                else:
                    print(f"✓ terminal_captures has required columns")
                    return True
            else:
                print("✗ terminal_captures table missing")
                return False
    except Exception as e:
        print(f"✗ Database check failed: {e}")
        return False

def check_session_manager():
    """Check session manager dependencies."""
    try:
        from flashback_terminal.session_manager import SessionManager

        # This will check dependencies
        manager = SessionManager()

        mode = manager.config.session_manager_mode
        print(f"✓ Session manager initialized with mode: {mode}")

        # Check that client capture is disabled
        if not manager.client_capture_disabled:
            print("⚠ Client capture not disabled in session manager")

        return True
    except Exception as e:
        print(f"✗ Session manager check failed: {e}")
        return False

def check_capture_worker():
    """Check capture worker can be instantiated."""
    try:
        from flashback_terminal.database import Database
        from flashback_terminal.workers.capture_worker import CaptureWorker
        from flashback_terminal.config import get_config

        config = get_config()
        db = Database(config.db_path)
        worker = CaptureWorker(db)

        print(f"✓ Capture worker instantiated (renderer: {worker._has_renderer})")

        # Check config properties
        enabled = worker.enabled
        interval = worker.interval_seconds

        print(f"  Capture enabled: {enabled}, interval: {interval}s")

        return True
    except Exception as e:
        print(f"✗ Capture worker check failed: {e}")
        return False

def check_templates():
    """Check timeline templates exist."""
    template_dir = PROJECT_ROOT / "flashback_terminal" / "templates"

    required = ['timeline.html', 'capture_detail.html']
    missing = []

    for template in required:
        path = template_dir / template
        if path.exists():
            print(f"✓ Template: {template}")
        else:
            print(f"✗ Template missing: {template}")
            missing.append(template)

    return len(missing) == 0

def check_api_endpoints():
    """Check server defines timeline API endpoints."""
    try:
        from flashback_terminal.server import app

        # Check route registration
        routes = [route.path for route in app.routes]

        required_routes = [
            '/api/v1/captures/timeline',
            '/api/v1/captures/by-id/{capture_id}',
            '/api/v1/captures/by-id/{capture_id}/neighbors',
            '/api/v1/captures/{capture_id}/screenshot',
            '/timeline',
            '/capture/{capture_id}',
        ]

        missing = []
        for route in required_routes:
            if route not in routes:
                # Check with regex pattern
                found = False
                for r in routes:
                    if route.replace('{capture_id}', '{capture_id}') in r:
                        found = True
                        break
                if not found:
                    missing.append(route)

        if missing:
            print(f"✗ Missing API endpoints: {missing}")
            print(f"  Available routes: {routes}")
            return False
        else:
            print("✓ All required API endpoints defined")
            return True
    except Exception as e:
        print(f"✗ API endpoint check failed: {e}")
        return False

def main():
    print("=" * 70)
    print("Flashback-Terminal Integration Validation")
    print("=" * 70)

    checks = [
        ("Imports", check_imports),
        ("Configuration", check_config),
        ("Database Schema", check_database_schema),
        ("Session Manager", check_session_manager),
        ("Capture Worker", check_capture_worker),
        ("Templates", check_templates),
        ("API Endpoints", check_api_endpoints),
    ]

    results = []
    for name, check in checks:
        print(f"\n{name}:")
        try:
            success = check()
            results.append((name, success))
        except Exception as e:
            print(f"  ✗ Exception: {e}")
            results.append((name, False))

    print("\n" + "=" * 70)
    print("Summary:")
    print("=" * 70)

    all_passed = True
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{name:20} {status}")
        if not success:
            all_passed = False

    print("\n" + "=" * 70)
    if all_passed:
        print("SUCCESS: All integration checks passed!")
        print("\nNext steps:")
        print("1. Install optional dependency for screenshot rendering:")
        print("   pip install agg_python_bindings")
        print("2. Start the server:")
        print("   python -m flashback_terminal.cli server")
        print("3. Open timeline in browser:")
        print("   http://localhost:9090/timeline")
    else:
        print("FAILURE: Some checks failed. Review errors above.")
        sys.exit(1)

if __name__ == "__main__":
    main()