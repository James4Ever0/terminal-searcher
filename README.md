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
git clone https://github.com/james4ever0/flashback-terminal.git
cd flashback-terminal
pip install -e .
```

### Development Install

```bash
git clone https://github.com/james4ever0/flashback-terminal.git
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

# Start with increased verbosity (-v, -vv, -vvv, or -vvvv)
flashback-terminal -vvv serve

# Initialize configuration
flashback-terminal init

# Check dependencies
flashback-terminal check

# Test embedding API configuration
flashback-terminal config test-embedding --write
```

#### Verbosity Levels

Use `-v`, `-vv`, `-vvv`, or `-vvvv` flags to control output verbosity:

| Flag | Level | Description |
|------|-------|-------------|
| (none) | ERROR | Only show errors |
| `-v` | WARNING | Show warnings and errors |
| `-vv` | INFO | Show general info (default) |
| `-vvv` | DEBUG | Show debug information |
| `-vvvv` | TRACE | Show all details including function calls |

Example with verbose logging:
```bash
flashback-terminal -vvvv serve --port 8080
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

# Logging verbosity: 0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG, 4=TRACE
logging:
  verbosity: 2
  log_to_file: false
  log_file: null

server:
  host: "127.0.0.1"
  port: 8080

terminal:
  rows: 24
  cols: 80
  shell: null  # Uses $SHELL by default
  login_shell: true

# Session manager: tmux | screen (REQUIRED)
session_manager:
  mode: tmux
  disable_client_capture: true
  screen:
    socket_dir: "~/.flashback-terminal/screen"
    binary: "screen"
  tmux:
    socket_dir: "~/.flashback-terminal/tmux"
    binary: "tmux"
    nested_session_env:
      TMUX: ""
      TMUX_PANE: ""
  capture:
    enabled: true
    interval_seconds: 10

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

#### `logging`
- `verbosity`: Logging level (0=ERROR, 1=WARNING, 2=INFO, 3=DEBUG, 4=TRACE). Higher levels include all lower level messages.
- `log_to_file`: Whether to also log to a file (default: false)
- `log_file`: Path to log file (null = auto-generate in data_dir)

Verbosity levels:
- **0 (ERROR)**: Only errors
- **1 (WARNING)**: Errors and warnings
- **2 (INFO)**: General operational information (default)
- **3 (DEBUG)**: Detailed debugging information, function calls
- **4 (TRACE)**: Very detailed tracing, function entry/exit with parameters

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

#### `session_manager`
Controls how terminal sessions are created and managed. **REQUIRES tmux or screen to be installed.**

- `mode`: Session management mode ("tmux" or "screen")
  - **tmux**: Use Tmux for session management (recommended)
  - **screen**: Use GNU Screen for session management
- `disable_client_capture`: Disable frontend terminal capture, use backend only (default: true)

**Screen mode options:**
- `screen.socket_dir`: Directory for screen sockets (default: "~/.flashback-terminal/screen")
- `screen.binary`: Screen binary name or path (default: "screen")
- `screen.config_file`: Path to custom screenrc (null = default). For kiosk mode use `escape ''`

**Tmux mode options:**
- `tmux.socket_dir`: Directory for tmux sockets (default: "~/.flashback-terminal/tmux")
- `tmux.binary`: Tmux binary name or path (default: "tmux")
- `tmux.config_file`: Path to custom tmux config (null = default)
- `tmux.nested_session_env`: Environment variables to unset for nested session support

**Backend capture options:**
- `capture.enabled`: Enable server-side session capture (default: true)
- `capture.interval_seconds`: Capture interval (default: 10)
- `capture.capture_full_scrollback`: Capture full scrollback history (default: true)

Example session_manager configuration:
```yaml
session_manager:
  mode: tmux
  disable_client_capture: true
  tmux:
    socket_dir: "~/.flashback-terminal/tmux"
    binary: "tmux"
    config_file: null
  capture:
    enabled: true
    interval_seconds: 10
```

To check if screen/tmux is installed:
```bash
flashback-terminal session-manager --validate
```

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
├── config.example.yaml     # Example configuration file
├── flashback_terminal/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py              # CLI commands
│   ├── config.py           # Configuration loader
│   ├── database.py         # Database models
│   ├── deps.py             # Dependency checker
│   ├── logger.py           # Logging system with verbosity
│   ├── retention.py        # Retention management
│   ├── search.py           # Search functionality
│   ├── server.py           # FastAPI application
│   ├── session_manager.py  # Session management (local/screen/tmux)
│   ├── terminal.py         # Terminal session wrapper
│   ├── api/
│   │   └── websocket.py    # WebSocket handler
│   ├── workers/
│   │   └── embedding_worker.py
│   ├── templates/          # Jinja2 templates
│   │   └── index.html      # Main UI template
│   └── static/
│       ├── index.html      # Fallback static HTML
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

### Q: What session manager should I use?

A: flashback-terminal **requires** either tmux or screen to be installed:

1. **tmux** (recommended): Modern terminal multiplexer with better capture support
2. **screen**: Traditional GNU Screen - also works well

Both enable:
- **Backend screenshot capture**: Server captures terminal content without frontend
- **Session persistence**: Sessions survive browser disconnections
- **Server-side text extraction**: OCR from terminal content

To use tmux:
```bash
# Install tmux
sudo apt-get install tmux  # Debian/Ubuntu
brew install tmux          # macOS

# Configure flashback-terminal
flashback-terminal init
# Edit ~/.config/flashback-terminal/config.yaml:
# session_manager:
#   mode: tmux
```

### Q: Why use tmux/screen instead of local mode?

A: Using tmux or screen enables:
- **Backend screenshot capture**: Server can capture terminal content without frontend
- **Session persistence**: Sessions survive browser disconnections
- **Better resource management**: External process handles the terminal

### Q: How do I check if tmux/screen is installed?

A: Run the validation command:
```bash
flashback-terminal session-manager --validate
```

Or check manually:
```bash
which tmux
which screen
```

### Q: Can I run flashback-terminal inside tmux?

A: Yes! When configured to use tmux mode, flashback-terminal automatically:
- Unsets TMUX environment variable for nested sessions
- Uses custom socket paths to avoid conflicts
- Configures tmux to work independently

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
