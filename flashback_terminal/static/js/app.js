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
        this.originalName = name; // Store original name
        this.terminal = null;
        this.socket = null;
        this.fitAddon = null;
        this.screenshotInterval = null;
        this.titleOverride = null; // Allow manual title override
        this.lastDetectedTitle = null; // Track auto-detected titles
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
                // Process escape sequences for title changes
                // this.processEscapeSequences(data);
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
                console.dir(this.terminal);
                break;
            case 'history_replay':
                for (const chunk of msg.chunks) {
                    this.terminal.write(chunk.content);
                }
                break;
            case "cursor":
                this.terminal.write("\x1b["+(msg.row)+";"+(msg.col+1)+"H");
                break;
            case 'clear':
                this.terminal.clear();
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
                this.updateTabTitle();
                
                // Show restoration notification if applicable
                if (msg.restored) {
                    this.terminal.writeln(`\r\n[flashback] Session restored successfully`);
                }
                break;
            case 'session_restored':
                this.terminal.writeln(`\r\n[flashback] ${msg.message}`);
                break;
            case 'session_unavailable':
                this.terminal.writeln(`\r\n[flashback] ${msg.message}`);
                break;
            case 'session_created':
                this.terminal.writeln(`\r\n[flashback] ${msg.message}`);
                break;
            case 'title_change':
                this.handleTitleChange(msg.title);
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

    handleTitleChange(title) {
        FrontendLogger.info(`Title change received: ${title}`);
        this.titleOverride = title;
        this.updateTabTitle();
    }

    updateTabTitle() {
        const displayName = this.titleOverride || this.name || this.originalName;
        FrontendLogger.debug(`Updating tab title to: ${displayName}`);
        
        // Update tab element if it exists
        if (this.app && this.app.activeTab === this) {
            this.app.renderTabs();
        }
        
        // Set window title if this is the active tab
        if (this.app && this.app.activeTab === this) {
            document.title = `${displayName} - flashback-terminal`;
        }
        
        // Send title change to backend to propagate to terminal
        if (this.socket && this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify({
                type: 'command',
                cmd: 'set_title',
                title: displayName
            }));
        }
    }

    setTitle(title) {
        FrontendLogger.info(`Manual title set: ${title}`);
        this.titleOverride = title;
        this.updateTabTitle();
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

        document.getElementById('btn-set-title').addEventListener('click', () => {
            FrontendLogger.debug('Set title button clicked');
            this.setActiveTabTitle();
        });
        document.getElementById('btn-search').addEventListener('click', () => this.openSearch());
        document.getElementById('btn-sessions').addEventListener('click', () => this.openSessions());

        document.querySelectorAll('.close-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.target.closest('.modal').classList.add('hidden');
            });
        });

        document.getElementById('btn-do-search').addEventListener('click', () => this.doSearch());

        // TODO: only if no tab to attach (all tabs in background are not running), we would create a new one instead. otherwise attach existing ones.
        // await this.createTab();
    }

    setActiveTabTitle() {
        if (!this.activeTab) {
            FrontendLogger.warn('No active tab to set title for');
            return;
        }
        
        const titleInput = document.getElementById('title-input');
        const newTitle = titleInput.value.trim();
        
        if (newTitle) {
            FrontendLogger.info(`Setting title for active tab: ${newTitle}`);
            this.activeTab.setTitle(newTitle);
            titleInput.value = ''; // Clear input after setting
        } else {
            FrontendLogger.warn('Empty title provided, ignoring');
        }
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
        tab.app = this; // Set app reference
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
        
        // Update window title to reflect active tab
        const displayName = tab.titleOverride || tab.name || tab.originalName;
        document.title = `${displayName} - flashback-terminal`;
    }

    renderTabs() {
        const container = document.getElementById('tabs');
        container.innerHTML = '';

        for (const tab of this.tabs) {
            const tabEl = document.createElement('div');
            tabEl.className = 'tab' + (tab === this.activeTab ? ' active' : '');
            const displayName = tab.titleOverride || tab.name || tab.originalName;
            tabEl.textContent = displayName;
            tabEl.title = `Session: ${tab.originalName}\nCurrent: ${displayName}\nUUID: ${tab.uuid}`;
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
        await this.loadSessions();
    }

    async loadSessions() {
        const response = await fetch('/api/sessions');
        const data = await response.json();

        const container = document.getElementById('sessions-list');
        
        if (data.sessions.length === 0) {
            container.innerHTML = '<div class="no-sessions">No sessions found</div>';
            return;
        }

        container.innerHTML = `
            <div class="sessions-header">
                <div class="sessions-title">Sessions (${data.sessions.length})</div>
                <button class="btn-refresh" onclick="app.loadSessions()">Refresh</button>
            </div>
        ` + data.sessions.map(s => {
            const createdDate = new Date(s.created_at);
            const formattedDate = createdDate.toLocaleString();
            
            // Determine what actions are available for this session
            let actionButtons = '';
            
            if (s.is_running) {
                // Session is already running, can switch to it
                actionButtons = `
                    <button class="btn-switch" onclick="app.switchToSession('${s.uuid}')">Switch To</button>
                `;
            } else if (s.can_attach) {
                // Session can be attached/restored
                actionButtons = `
                    <button class="btn-attach" onclick="app.attachToSession('${s.uuid}')">Attach</button>
                    <button class="btn-restore" onclick="app.restoreSession('${s.uuid}')">Restore</button>
                `;
            } else {
                // Session is not available for attachment
                actionButtons = `
                    <span class="status-unavailable">Unavailable</span>
                `;
            }

            return `
                <div class="session-item">
                    <div class="session-info">
                        <div class="session-name">${s.name}</div>
                        <div class="session-details">
                            <div class="session-uuid">UUID: ${s.uuid}</div>
                            <div class="session-created">Created: ${formattedDate}</div>
                            ${s.last_cwd ? `<div class="session-cwd">Last CWD: ${s.last_cwd}</div>` : ''}
                            <div class="session-profile">Profile: ${s.profile_name}</div>
                        </div>
                    </div>
                    <div class="session-status">
                        <span class="status ${s.status}">${s.status}</span>
                        <div class="session-actions">
                            ${actionButtons}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    async switchToSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.switchToSession');

        // Check if we already have a tab for this session
        const existingTab = this.tabs.find(t => t.uuid === sessionUuid);
        if (existingTab) {
            this.switchTab(existingTab);
            this.closeSessionsModal();
            return;
        }

        await this.attachToSession(sessionUuid);
        // this.closeSessionsModal();
    }

    async attachToSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.attachToSession');

        try {
            this.showLoading(`Attaching to session ${sessionUuid}...`);
            
            const response = await fetch(`/api/sessions/${sessionUuid}/attach`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to attach to session');
            }
            
            const result = await response.json();
            console.log('Attached to session:', result);
            
            // Create a new tab for the attached session
            const tab = new TerminalTab(result.uuid, result.name);
            tab.app=this;
            this.tabs.push(tab);
            await tab.connect();

            this.switchTab(tab);
            this.renderTabs();

            this.closeSessionsModal();
            this.hideLoading();
            
        } catch (error) {
            console.error('Failed to attach to session:', error);
            this.showError(`Failed to attach to session: ${error.message}`);
            this.hideLoading();
        }
    }

    async restoreSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.restoreSession');

        try {
            this.showLoading(`Restoring session ${sessionUuid}...`);
            
            const response = await fetch(`/api/sessions/${sessionUuid}/restore`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to attach to session');
            }
            
            const result = await response.json();
            console.log('Attached to session:', result);
            
            // Create a new tab for the attached session
            const tab = new TerminalTab(result.uuid, result.name);
            tab.app=this;
            this.tabs.push(tab);
            await tab.connect();

            this.switchTab(tab);
            this.renderTabs();

            this.closeSessionsModal();
            this.hideLoading();
        } catch (error) {
            console.error('Failed to restore session:', error);
            this.showError(`Failed to restore session: ${error.message}`);
            this.hideLoading();
        }
    }

    closeSessionsModal() {
        document.getElementById('sessions-modal').classList.add('hidden');
    }

    showLoading(message) {
        // Show loading indicator (you can implement this as needed)
        console.log('Loading:', message);
    }

    hideLoading() {
        // Hide loading indicator
        console.log('Loading complete');
    }

    showError(message) {
        // Show error message (you can implement this as needed)
        alert(message); // Simple implementation, you might want to use a better UI
    }
}

const app = new App();
app.init();
