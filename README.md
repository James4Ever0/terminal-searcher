# flashback-terminal

A recoverable web-based terminal with semantic search and session history. Built with Python (FastAPI) and xterm.js.

## Features

- **Web-based Terminal**: Access your terminal through any modern browser
- **Session Recovery**: Restore previous sessions with full history replay
- **Global Search**: Search across all terminal sessions using BM25 text search or semantic embeddings
- **Screenshot Capture**: Automatic terminal screenshots captured every 10 seconds
- **Multiple Tabs**: Manage multiple terminal sessions in tabs
- **Configurable Retention**: Archive or delete old sessions based on age and size constraints
- **Modular Design**: Enable/disable features as needed

## Installation

### From PyPI

```bash
pip install flashback-terminal
```

### From Source

```bash
git clone https://github.com/yourusername/flashback-terminal.git
cd flashback-terminal
pip install -e .
```

### Development Install

```bash
git clone https://github.com/yourusername/flashback-terminal.git
cd flashback-terminal
pip install -e ".[dev]"
```

## Quick Start

1. **Initialize configuration**:
   ```bash
   flashback-terminal init
   ```

2. **Start the server**:
   ```bash
   flashback-terminal serve
   ```

3. **Open your browser** and navigate to `http://localhost:8080`

## Downloading xterm.js (Local Copy)

To use a local copy of xterm.js instead of CDN:

```bash
# Create the vendor directory
mkdir -p flashback_terminal/static/js/vendor
mkdir -p flashback_terminal/static/css/vendor

# Download xterm.js and addons
curl -L -o flashback_terminal/static/js/vendor/xterm.js \
  https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js

curl -L -o flashback_terminal/static/css/vendor/xterm.css \
  https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css

curl -L -o flashback_terminal/static/js/vendor/xterm-addon-fit.js \
  https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js
```

Then update `index.html` to use the local paths:
```html
<link rel="stylesheet" href="/static/css/vendor/xterm.css">
<script src="/static/js/vendor/xterm.js"></script>
<script src="/static/js/vendor/xterm-addon-fit.js"></script>
```

## Usage

### CLI Commands

```bash
# Start the server
flashback-terminal serve

# Start on custom host/port
flashback-terminal serve --host 0.0.0.0 --port 3000

# Initialize configuration
flashback-terminal init

# Check dependencies
flashback-terminal check

# Test embedding API configuration
flashback-terminal config test-embedding --write
```

### Web Interface

- **New Tab**: Click the "+ New Tab" button to create a new terminal session
- **Search**: Click "Search" to search through terminal history
- **Sessions**: Click "Sessions" to view and manage all sessions
- **Rename Tab**: Send the command `{type: "command", cmd: "rename", name: "new name"}` via WebSocket

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+T` | New tab (when implemented in frontend) |
| `Ctrl+Shift+F` | Open search |

## Configuration

Configuration is stored in `~/.config/flashback-terminal/config.yaml`.

### Example Configuration

```yaml
data_dir: "~/.local/share/flashback-terminal"

server:
  host: "127.0.0.1"
  port: 8080

terminal:
  rows: 24
  cols: 80
  shell: null  # Uses $SHELL by default
  login_shell: true

modules:
  history_keeper:
    enabled: true
    use_fts: true

  screenshot_capture:
    enabled: true
    max_per_session: 1000

  semantic_search:
    enabled: false

  session_recovery:
    enabled: true
    max_recovery_age_days: 30

workers:
  retention:
    enabled: true
    history_days: 10
    strategy: "archive"  # or "delete"
    archive:
      total_size_limit: 10737418240  # 10 GB
      max_age_days: 90
      compression: "gzip"

search:
  enabled_methods:
    bm25: true
    embedding: false

profiles:
  - name: "default"
    shell: null
    args: []
    cwd: "~"

  - name: "python"
    shell: "/usr/bin/python3"
    args: ["-i"]
    description: "Python REPL"
```

### Configuration Options

#### `data_dir`
Base directory for all data storage (database, logs, screenshots, archives).

#### `server`
- `host`: Server bind address (default: "127.0.0.1")
- `port`: Server port (default: 8080)

#### `terminal`
- `rows`: Default terminal rows (default: 24)
- `cols`: Default terminal columns (default: 80)
- `shell`: Shell command (null = use $SHELL)
- `login_shell`: Use login shell (-l flag)

#### `modules.history_keeper`
- `enabled`: Enable terminal output logging
- `buffer_size_lines`: Buffer size for efficient storage
- `use_fts`: Enable full-text search index (SQLite FTS5)

#### `modules.screenshot_capture`
- `enabled`: Enable screenshot capture
- `max_per_session`: Maximum screenshots per session (0 = unlimited)
- `max_file_size_mb`: Maximum screenshot file size

#### `modules.semantic_search`
- `enabled`: Enable semantic search (requires embedding worker)
- `text_weight`: Weight for text embeddings in hybrid search
- `bm25_weight`: Weight for BM25 in hybrid search
- `rrf_k`: Reciprocal Rank Fusion k value

#### `modules.session_recovery`
- `enabled`: Enable session recovery features
- `max_recovery_age_days`: Maximum age of recoverable sessions
- `replay_mode`: "instant" or "animated" history replay

#### `workers.retention`
- `enabled`: Enable automatic cleanup
- `history_days`: Days to keep sessions before archiving/deleting
- `strategy`: "archive" or "delete"
- `archive.total_size_limit`: Maximum total archive size (bytes)
- `archive.max_age_days`: Maximum age of archives
- `archive.compression`: "gzip", "bz2", "xz", or null
- `archive.organization`: "flat", "monthly", or "yearly"

#### `workers.embedding`
- `enabled`: Enable embedding generation
- `mode`: "text-only" (currently only mode supported)
- `text.base_url`: OpenAI-compatible API URL
- `text.api_key`: API key (supports `${ENV_VAR}` syntax)
- `text.model`: Embedding model name
- `text.dimension`: Embedding dimension (auto-detected)

#### `profiles`
Array of terminal profiles with:
- `name`: Profile identifier
- `shell`: Shell executable
- `args`: Shell arguments
- `env`: Environment variables
- `cwd`: Working directory
- `description`: Human-readable description

## Data Storage

All data is stored in `~/.local/share/flashback-terminal/`:

```
~/.local/share/flashback-terminal/
├── terminal.db          # SQLite database
├── logs/                # Terminal raw output logs
├── screenshots/         # Terminal screenshots
├── embeddings/          # Vector embeddings (optional)
└── archive/             # Archived sessions
    ├── archive.inprogress/
    └── 2024-01/         # Monthly archives
```

## API Endpoints

### WebSocket
- `WS /ws/terminal/{session_uuid}` - Terminal I/O stream

### REST
- `GET /api/sessions` - List sessions
- `POST /api/sessions` - Create new session
- `GET /api/sessions/{uuid}` - Get session details
- `PUT /api/sessions/{uuid}` - Update session (rename)
- `DELETE /api/sessions/{uuid}` - Delete session
- `POST /api/sessions/{uuid}/restore` - Restore archived session
- `GET /api/profiles` - List profiles
- `POST /api/search` - Search terminal history
- `GET /api/history/{uuid}` - Get session history
- `GET /api/screenshots/{uuid}` - List screenshots
- `POST /api/retention/run` - Trigger retention manually

## Development

### Project Structure

```
flashback-terminal/
├── pyproject.toml
├── README.md
├── flashback_terminal/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py              # CLI commands
│   ├── config.py           # Configuration loader
│   ├── database.py         # Database models
│   ├── deps.py             # Dependency checker
│   ├── retention.py        # Retention management
│   ├── search.py           # Search functionality
│   ├── server.py           # FastAPI application
│   ├── terminal.py         # PTY management
│   ├── api/
│   │   └── websocket.py    # WebSocket handler
│   ├── workers/
│   │   └── embedding_worker.py
│   └── static/
│       ├── index.html
│       ├── css/
│       │   └── style.css
│       └── js/
│           └── app.js
```

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black flashback_terminal/
ruff check flashback_terminal/
```

### Adding a New Profile

1. Edit `~/.config/flashback-terminal/config.yaml`
2. Add a profile to the `profiles` list
3. Restart the server

Example:
```yaml
profiles:
  - name: "nodejs"
    shell: "/usr/bin/node"
    args: []
    env:
      NODE_ENV: "development"
    cwd: "~/projects"
    description: "Node.js REPL"
```

### Implementing a New Worker

1. Create a new file in `flashback_terminal/workers/`
2. Implement `start()` and `stop()` methods
3. Add configuration options to `config.py`
4. Initialize in `server.py` lifespan

## FAQ

### Q: How do I enable semantic search?

A: You need an OpenAI-compatible embedding API:

```yaml
workers:
  embedding:
    enabled: true
    text:
      base_url: "https://api.openai.com/v1"
      api_key: "${OPENAI_API_KEY}"
      model: "text-embedding-3-small"

modules:
  semantic_search:
    enabled: true

search:
  enabled_methods:
    embedding: true
```

Then run:
```bash
flashback-terminal config test-embedding --write
```

### Q: Can I use Ollama for embeddings?

A: Yes! Configure it like this:

```yaml
workers:
  embedding:
    enabled: true
    text:
      base_url: "http://localhost:11434/v1"
      api_key: ""
      model: "nomic-embed-text"
```

### Q: How does session recovery work?

A: When you reconnect to a session:
1. All previous terminal output is replayed
2. The system attempts to `cd` to the last working directory
3. If the directory no longer exists, you'll see: "despite retries, we could not cd to /path: No such file or directory"

### Q: How do I restore an archived session?

A: Currently, restoration must be done manually by extracting the archive from `~/.local/share/flashback-terminal/archive/`. Automatic restoration via the web UI is planned.

### Q: What's the difference between archive and delete retention?

A:
- **Delete**: Permanently removes old sessions
- **Archive**: Compresses and stores sessions in `~/.local/share/flashback-terminal/archive/`. Archives can be restored later and are subject to `total_size_limit` and `max_age_days` constraints.

### Q: Can I change the screenshot interval?

A: The screenshot interval is currently hardcoded to 10 seconds in the frontend (`app.js`). You can modify this by editing the JavaScript file.

### Q: Is it secure to expose flashback-terminal to the internet?

A: By default, the server binds to `127.0.0.1` (localhost only). To expose it:
1. Set `server.host: "0.0.0.0"` in config
2. Use a reverse proxy (nginx, traefik) with HTTPS
3. Add authentication (not built-in yet)

### Q: Can I use a different shell?

A: Yes, configure it in your profile:

```yaml
profiles:
  - name: "zsh"
    shell: "/usr/bin/zsh"
    args: ["-l"]
    cwd: "~"
```

### Q: How do I backup my data?

A: Backup these directories:
- `~/.local/share/flashback-terminal/terminal.db` (database)
- `~/.local/share/flashback-terminal/logs/` (terminal logs)
- `~/.local/share/flashback-terminal/screenshots/` (screenshots)
- `~/.local/share/flashback-terminal/archive/` (archives)

## Troubleshooting

### "Failed to create terminal session"

- Check that bash/sh is installed: `which bash`
- Verify permissions on the data directory

### "Embedding dimension not configured"

Run the configuration test:
```bash
flashback-terminal config test-embedding --write
```

### "Search not available"

Ensure `modules.history_keeper.enabled` is true and the database is initialized.

### Screenshots not saving

Check browser console for errors. Ensure the screenshot file size is under `max_file_size_mb`.

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Roadmap

- [ ] Authentication system
- [ ] File upload/download via terminal
- [ ] Collaborative sessions
- [ ] Mobile-responsive UI
- [ ] Plugin system
- [ ] Export session as video (from screenshots)
- [ ] Better CWD tracking (shell integration)
