"""CLI for flashback-terminal."""

import signal
import sys
from pathlib import Path

import click
import uvicorn

from flashback_terminal.config import USER_CONFIG_DIR, USER_CONFIG_PATH, get_config
from flashback_terminal.deps import DependencyChecker


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """flashback-terminal - Recoverable web terminal with semantic search."""
    pass


@cli.command()
@click.option("--host", default=None, help="Server host")
@click.option("--port", default=None, type=int, help="Server port")
@click.option("--config", "-c", type=click.Path(), help="Path to config file")
def serve(host, port, config):
    """Start the flashback-terminal server."""
    config_path = Path(config) if config else None
    cfg = get_config(config_path)

    DependencyChecker.check_all()

    server_host = host or cfg.server_host
    server_port = port or cfg.server_port

    click.echo(f"Starting flashback-terminal on http://{server_host}:{server_port}")

    from flashback_terminal.server import app

    uvicorn.run(app, host=server_host, port=server_port)


@cli.command()
def init():
    """Initialize flashback-terminal configuration."""
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    if USER_CONFIG_PATH.exists():
        click.echo(f"Config already exists at {USER_CONFIG_PATH}")
        return

    default_config = """# flashback-terminal configuration
data_dir: "~/.local/share/flashback-terminal"

server:
  host: "127.0.0.1"
  port: 8080

terminal:
  rows: 24
  cols: 80
  shell: null

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


def main():
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
