// flashback-terminal frontend application
// Verbosity level injected by server via Jinja2: window.VERBOSITY_LEVEL

// Frontend logger with verbosity control
const FrontendLogger = {
    // Verbosity levels (0=ERROR, 1=WARN, 2=INFO, 3=DEBUG, 4=TRACE)
    ERROR: 0,
    WARN: 1,
    INFO: 2,
    DEBUG: 3,
    TRACE: 4,

    // Get verbosity from server-injected value or default to INFO
    getVerbosity() {
        return (typeof window !== 'undefined' && window.VERBOSITY_LEVEL !== undefined)
            ? window.VERBOSITY_LEVEL
            : 2;
    },

    shouldLog(level) {
        return this.getVerbosity() >= level;
    },

    log(level, levelName, ...args) {
        if (!this.shouldLog(level)) return;

        const timestamp = new Date().toISOString();
        const prefix = `[${timestamp}] [${levelName}] [Frontend]`;

        if (level === this.ERROR) {
            console.error(prefix, ...args);
        } else if (level === this.WARN) {
            console.warn(prefix, ...args);
        } else {
            console.log(prefix, ...args);
        }
    },

    error(...args) { this.log(this.ERROR, 'ERROR', ...args); },
    warn(...args) { this.log(this.WARN, 'WARN', ...args); },
    info(...args) { this.log(this.INFO, 'INFO', ...args); },
    debug(...args) { this.log(this.DEBUG, 'DEBUG', ...args); },
    trace(...args) { this.log(this.TRACE, 'TRACE', ...args); },

    // Log function entry/exit
    logFunction(funcName, params = null) {
        if (this.shouldLog(this.TRACE)) {
            this.trace(`[ENTER] ${funcName}`, params ? `params=${JSON.stringify(params)}` : '');
        }
        return () => {
            if (this.shouldLog(this.TRACE)) {
                this.trace(`[EXIT] ${funcName}`);
            }
        };
    }
};

// Log initial verbosity
FrontendLogger.info(`Frontend initialized with verbosity level ${FrontendLogger.getVerbosity()}`);

class TerminalTab {
    constructor(uuid, name) {
        FrontendLogger.debug(`TerminalTab constructor: uuid=${uuid}, name=${name}`);
        this.uuid = uuid;
        this.name = name;
        this.terminal = null;
        this.socket = null;
        this.fitAddon = null;
        this.screenshotInterval = null;
    }

    async connect() {
        // TODO: add more visual feedback for connection details, like "connecting", "error from backend: xxx", "disconnected", "terminated"
        FrontendLogger.info(`Connecting terminal tab: uuid=${this.uuid}`);
        const exitLog = FrontendLogger.logFunction('TerminalTab.connect', { uuid: this.uuid });

        // best way to see the cursor: turn dark reader off.

        this.terminal = new Terminal({
            fontFamily: "'Courier New', monospace",
            fontSize: 14,
            theme: {
                background: '#1a1a1a',
                foreground: '#eee'
            },
            cursorBlink: true,
            convertEol: true
        });

        this.fitAddon = new FitAddon.FitAddon();
        this.terminal.loadAddon(this.fitAddon);

        const container = document.getElementById('terminal-container');
        this.terminal.open(container);
        this.fitAddon.fit();

        const wsUrl = `ws://${window.location.host}/ws/terminal/${this.uuid}`;
        this.socket = new WebSocket(wsUrl);

        this.socket.onopen = () => {
            FrontendLogger.info(`WebSocket connected: uuid=${this.uuid}`);
            this.startScreenshotCapture();
            // resize terminal initially.
            this.fitAddon.fit();
            this.sendResize();
        };

        this.socket.onmessage = (event) => {
            FrontendLogger.trace('WebSocket message received:', event.data.substring(0, 200));
            const msg = JSON.parse(event.data);
            this.handleMessage(msg);
        };

        this.socket.onclose = () => {
            FrontendLogger.info(`WebSocket closed: uuid=${this.uuid}`);
            this.stopScreenshotCapture();
        };

        // Send input to PTY - PTY will echo back for display
        this.terminal.onData((data) => {
            if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                this.socket.send(JSON.stringify({
                    type: 'input',
                    data: data
                }));
            }
        });

        window.addEventListener('resize', () => {
            this.fitAddon.fit();
            this.sendResize();
        });

        window.addEventListener('focus', () => {
            this.fitAddon.fit();
            this.sendResize();
        });
    }

    handleMessage(msg) {
        switch (msg.type) {
            case 'output':
                this.terminal.write(msg.data);
                break;
            case 'history_replay':
                for (const chunk of msg.chunks) {
                    this.terminal.write(chunk.content);
                }
                break;
            case 'cwd_change':
                if (!msg.success) {
                    this.terminal.writeln(`\r\n[flashback] ${msg.error}`);
                }
                break;
            case 'error':
                this.terminal.writeln(`\r\n[Error] ${msg.message}`);
                break;
            case 'session_info':
                this.uuid = msg.uuid;
                this.name = msg.name;
                break;
        }
    }

    sendResize() {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify({
                type: 'resize',
                rows: this.terminal.rows,
                cols: this.terminal.cols
            }));
        }
    }

    startScreenshotCapture() {
        const interval = 10000;
        this.screenshotInterval = setInterval(() => {
            this.captureAndUpload();
        }, interval);
    }

    stopScreenshotCapture() {
        if (this.screenshotInterval) {
            clearInterval(this.screenshotInterval);
            this.screenshotInterval = null;
        }
    }

    captureAndUpload() {
        const canvas = this.terminal.element.querySelector('canvas');
        if (!canvas) return;

        canvas.toBlob((blob) => {
            const reader = new FileReader();
            reader.onloadend = () => {
                const base64data = reader.result;
                if (this.socket && this.socket.readyState === WebSocket.OPEN) {
                    this.socket.send(JSON.stringify({
                        type: 'command',
                        cmd: 'screenshot_upload',
                        timestamp: new Date().toISOString(),
                        data: base64data
                    }));
                }
            };
            reader.readAsDataURL(blob);
        }, 'image/png');
    }

    focus() {
        this.terminal.focus();
    }

    dispose() {
        this.stopScreenshotCapture();
        if (this.socket) {
            this.socket.close();
        }
        this.terminal.dispose();
    }
}

class App {
    constructor() {
        FrontendLogger.debug('App constructor');
        this.tabs = [];
        this.activeTab = null;
    }

    async init() {
        FrontendLogger.info('App initializing...');
        const exitLog = FrontendLogger.logFunction('App.init');

        document.getElementById('btn-new-tab').addEventListener('click', () => {
            FrontendLogger.debug('New tab button clicked');
            this.createTab();
        });
        document.getElementById('btn-search').addEventListener('click', () => this.openSearch());
        document.getElementById('btn-sessions').addEventListener('click', () => this.openSessions());

        document.querySelectorAll('.close-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.target.closest('.modal').classList.add('hidden');
            });
        });

        document.getElementById('btn-do-search').addEventListener('click', () => this.doSearch());

        await this.createTab();
    }

    async createTab() {
        FrontendLogger.info('Creating new tab...');
        const exitLog = FrontendLogger.logFunction('App.createTab');

        const response = await fetch('/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({profile: 'default'})
        });

        if (!response.ok) {
            FrontendLogger.error('Failed to create session:', response.statusText);
            return;
        }

        const data = await response.json();
        FrontendLogger.info(`Session created: uuid=${data.uuid}`);

        const tab = new TerminalTab(data.uuid, data.name);
        this.tabs.push(tab);
        await tab.connect();
        this.switchTab(tab);
        this.renderTabs();
    }

    switchTab(tab) {
        if (this.activeTab) {
            this.activeTab.terminal.element.style.display = 'none';
        }
        this.activeTab = tab;
        tab.terminal.element.style.display = 'block';
        tab.focus();
        this.renderTabs();
    }

    renderTabs() {
        const container = document.getElementById('tabs');
        container.innerHTML = '';

        for (const tab of this.tabs) {
            const tabEl = document.createElement('div');
            tabEl.className = 'tab' + (tab === this.activeTab ? ' active' : '');
            tabEl.textContent = tab.name;
            tabEl.addEventListener('click', () => this.switchTab(tab));
            container.appendChild(tabEl);
        }
    }

    openSearch() {
        document.getElementById('search-modal').classList.remove('hidden');
    }

    async doSearch() {
        const query = document.getElementById('search-input').value;
        const mode = document.getElementById('search-mode').value;
        const scope = document.getElementById('search-scope').value;

        FrontendLogger.info(`Search initiated: query="${query}", mode=${mode}, scope=${scope}`);
        const exitLog = FrontendLogger.logFunction('App.doSearch', { query, mode, scope });

        // Show searching feedback
        this.renderSearchStatus('Searching...');

        const sessionIds = scope === 'current' && this.activeTab ? [this.activeTab.uuid] : [];

        try {
            const response = await fetch('/api/search', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    query: query,
                    mode: mode,
                    scope: scope,
                    session_ids: sessionIds
                })
            });

            if (!response.ok) {
                this.renderSearchStatus('Request failed');
                return;
            }

            const data = await response.json();
            this.renderSearchResults(data.results);
        } catch (error) {
            this.renderSearchStatus('Request failed');
        }
    }

    renderSearchStatus(message) {
        const container = document.getElementById('search-results');
        container.innerHTML = `<div class="search-status">${message}</div>`;
    }

    renderSearchResults(results) {
        const container = document.getElementById('search-results');

        if (!results || results.length === 0) {
            container.innerHTML = '<div class="search-status">No results found</div>';
            return;
        }

        const countFeedback = `<div class="search-count">${results.length} result${results.length !== 1 ? 's' : ''} found</div>`;

        // Get set of running terminal UUIDs
        const runningUuids = new Set(this.tabs.map(tab => tab.uuid));

        const resultsHtml = results.map(r => {
            const isRunning = runningUuids.has(r.session_uuid);
            const buttonClass = isRunning ? 'jump-btn' : 'jump-btn disabled';
            const buttonText = isRunning ? 'Jump to Terminal' : 'Terminal Not Available';

            return `
            <div class="search-result">
                <div class="result-header">
                    <span class="session-name">${r.session_name}</span>
                    <span class="timestamp">${r.timestamp}</span>
                </div>
                <pre class="result-content">${r.content.substring(0, 200)}...</pre>
                <button class="${buttonClass}" data-uuid="${r.session_uuid}" ${isRunning ? '' : 'disabled'}>
                    ${buttonText}
                </button>
            </div>
        `}).join('');

        container.innerHTML = countFeedback + resultsHtml;

        // Add click handlers for jump buttons
        container.querySelectorAll('.jump-btn:not(.disabled)').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const uuid = e.target.getAttribute('data-uuid');
                this.jumpToTerminal(uuid);
            });
        });
    }

    jumpToTerminal(uuid) {
        const tab = this.tabs.find(t => t.uuid === uuid);
        if (tab) {
            this.switchTab(tab);
            // Close search modal
            document.getElementById('search-modal').classList.add('hidden');
        }
    }

    async openSessions() {
        document.getElementById('sessions-modal').classList.remove('hidden');

        const response = await fetch('/api/sessions');
        const data = await response.json();

        const container = document.getElementById('sessions-list');
        container.innerHTML = data.sessions.map(s => `
            <div class="session-item">
                <span>${s.name}</span>
                <span class="status ${s.status}">${s.status}</span>
            </div>
        `).join('');
    }
}

const app = new App();
app.init();
