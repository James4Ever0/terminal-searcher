"""CLI for flashback-terminal."""

import os
import sys
from pathlib import Path

import click
import uvicorn

from flashback_terminal.config import USER_CONFIG_DIR, USER_CONFIG_PATH, get_config
from flashback_terminal.deps import DependencyChecker
from flashback_terminal.logger import Logger


def setup_logging_from_config():
    """Setup logging based on configuration."""
    cfg = get_config()
    verbosity = cfg.verbosity
    Logger.set_verbosity(verbosity)
    return verbosity


@click.group()
@click.version_option(version="0.1.0")
@click.option(
    "-v", "--verbose",
    count=True,
    default=0,
    help="Increase verbosity level (use -v, -vv, -vvv, or -vvvv). "
         "0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG, 4=TRACE"
)
@click.option(
    "--config", "-c",
    type=click.Path(),
    help="Path to config file"
)
@click.pass_context
def cli(ctx, verbose, config):
    """flashback-terminal - Recoverable web terminal with semantic search.

    Verbosity levels:
      (none)  - Show errors only
      -v      - Show warnings
      -vv     - Show info messages (default)
      -vvv    - Show debug messages
      -vvvv   - Show trace messages (all details)
    """
    # Store config path in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config

    # Setup logging
    if config:
        get_config(Path(config))

    cfg = get_config()

    # Command line verbosity overrides config
    if verbose > 0:
        Logger.set_verbosity(verbose)
        cfg.set("logging.verbosity", verbose)
        os.environ['CLI_VERBOSITY'] = str(verbose)
    else:
        Logger.set_verbosity(cfg.verbosity)


@cli.command()
@click.option("--host", default=None, help="Server host (overrides config)")
@click.option("--port", default=None, type=int, help="Server port (overrides config)")
@click.pass_context
def serve(ctx, host, port):
    """Start the flashback-terminal server."""
    from flashback_terminal.logger import log_function, logger

    config_path = ctx.obj.get('config_path') if ctx.obj else None
    if config_path:
        config_path = Path(config_path)

    cfg = get_config(config_path)
    setup_logging_from_config()

    logger.info("Starting flashback-terminal server")
    logger.debug(f"Config path: {config_path or 'default'}")

    DependencyChecker.check_all()

    server_host = host or cfg.server_host
    server_port = port or cfg.server_port

    logger.info(f"Server configured for {server_host}:{server_port}")

    click.echo(f"Starting flashback-terminal on http://{server_host}:{server_port}")

    from flashback_terminal.server import app

    uvicorn.run(app, host=server_host, port=server_port)


@cli.command()
@click.pass_context
def init(ctx):
    """Initialize flashback-terminal configuration."""
    from flashback_terminal.logger import logger

    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Initializing configuration at {USER_CONFIG_DIR}")

    if USER_CONFIG_PATH.exists():
        click.echo(f"Config already exists at {USER_CONFIG_PATH}")
        logger.warning(f"Config file already exists: {USER_CONFIG_PATH}")
        return

    default_config = """# flashback-terminal configuration
# Logging verbosity: 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG, 4=TRACE
logging:
  verbosity: 2
  log_to_file: false
  log_file: null

data_dir: "~/.local/share/flashback-terminal"

server:
  host: "127.0.0.1"
  port: 8080

terminal:
  rows: 24
  cols: 80
  shell: null

# Session manager configuration (REQUIRED: tmux or screen)
# mode: "tmux" | "screen"
#   - tmux: Use Tmux for session management (recommended)
#   - screen: Use GNU Screen for session management
# Requires: sudo apt-get install tmux  (or screen)
session_manager:
  mode: tmux
  disable_client_capture: true  # Disable frontend capture, use backend only
  screen:
    socket_dir: "~/.flashback-terminal/screen"
    binary: "screen"
    config_file: null
  tmux:
    socket_dir: "~/.flashback-terminal/tmux"
    binary: "tmux"
    config_file: null
    nested_session_env:
      TMUX: ""
      TMUX_PANE: ""
      TMUX_WINDOW: ""
  capture:
    enabled: true
    interval_seconds: 10
    max_captures_per_session: 1000
    capture_full_scrollback: true

modules:
  history_keeper:
    enabled: true
  screenshot_capture:
    enabled: true
  semantic_search:
    enabled: false
  session_recovery:
    enabled: true

workers:
  retention:
    enabled: true
    history_days: 10
    strategy: "archive"

search:
  enabled_methods:
    bm25: true
    embedding: false
"""
    USER_CONFIG_PATH.write_text(default_config, encoding="utf-8")
    click.echo(f"Created config at {USER_CONFIG_PATH}")
    logger.info(f"Created default config file: {USER_CONFIG_PATH}")


@cli.command()
@click.argument("action", type=click.Choice(["test-embedding"]))
@click.option("--type", "embedding_type", type=click.Choice(["text", "image"]), default="text")
@click.option("--write", "-w", is_flag=True, help="Write detected dimension to config")
def config_cmd(action, embedding_type, write):
    """Configuration utilities."""
    if action == "test-embedding":
        _test_embedding(embedding_type, write)


def _test_embedding(embedding_type: str, write: bool):
    """Test embedding API and detect dimension."""
    import os

    import requests

    cfg = get_config()

    if embedding_type == "text":
        api_config = cfg.get("workers.embedding.text", {})
    else:
        click.echo("Image embedding not yet supported")
        return

    base_url = api_config.get("base_url", "")
    model = api_config.get("model", "")
    api_key = api_config.get("api_key", "")

    if api_key.startswith("${") and api_key.endswith("}"):
        api_key = os.environ.get(api_key[2:-1], "")

    if not base_url:
        click.echo("ERROR: base_url not configured")
        return
    if not model:
        click.echo("ERROR: model not configured")
        return

    click.echo(f"Testing embedding API at {base_url}")
    click.echo(f"Model: {model}")

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response = requests.post(
            f"{base_url}/embeddings",
            headers=headers,
            json={"model": model, "input": "test"},
            timeout=60,
        )
        response.raise_for_status()

        data = response.json()
        dimension = len(data["data"][0]["embedding"])

        click.echo(f"✓ API connection successful!")
        click.echo(f"✓ Detected dimension: {dimension}")

        if write:
            import yaml

            config_text = USER_CONFIG_PATH.read_text(encoding="utf-8")
            config = yaml.safe_load(config_text)

            if embedding_type == "text":
                config["workers"]["embedding"]["text"]["dimension"] = dimension

            with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False)

            click.echo(f"✓ Wrote dimension to config file")

    except Exception as e:
        click.echo(f"✗ API test failed: {e}")


@cli.command()
def check():
    """Check dependencies and configuration."""
    DependencyChecker.check_all()
    click.echo("All dependencies satisfied!")


@cli.command("session-manager")
@click.option("--info", is_flag=True, help="Show session manager configuration")
@click.option("--validate", is_flag=True, help="Validate session manager dependencies")
def session_manager_cmd(info, validate):
    """Session manager utilities."""
    from flashback_terminal.logger import logger

    if info or (not info and not validate):
        DependencyChecker.print_session_manager_info()
        click.echo()

    if validate or (not info and not validate):
        cfg = get_config()
        errors = DependencyChecker.check_session_manager_deps(cfg)
        if errors:
            for e in errors:
                click.echo(f"✗ {e}", err=True)
            sys.exit(1)
        else:
            mode = cfg.session_manager_mode
            if mode == "local":
                click.echo("✓ Local PTY mode (no external dependencies)")
            else:
                click.echo(f"✓ {mode} dependencies satisfied")


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
