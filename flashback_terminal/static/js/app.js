// flashback-terminal frontend application

class TerminalTab {
    constructor(uuid, name) {
        this.uuid = uuid;
        this.name = name;
        this.terminal = null;
        this.socket = null;
        this.fitAddon = null;
        this.screenshotInterval = null;
    }

    async connect() {
        this.terminal = new Terminal({
            fontFamily: "'Courier New', monospace",
            fontSize: 14,
            theme: {
                background: '#1a1a1a',
                foreground: '#eee'
            }
        });

        this.fitAddon = new FitAddon.FitAddon();
        this.terminal.loadAddon(this.fitAddon);

        const container = document.getElementById('terminal-container');
        this.terminal.open(container);
        this.fitAddon.fit();

        const wsUrl = `ws://${window.location.host}/ws/terminal/${this.uuid}`;
        this.socket = new WebSocket(wsUrl);

        this.socket.onopen = () => {
            console.log('WebSocket connected');
            this.startScreenshotCapture();
        };

        this.socket.onmessage = (event) => {
            const msg = JSON.parse(event.data);
            this.handleMessage(msg);
        };

        this.socket.onclose = () => {
            console.log('WebSocket closed');
            this.stopScreenshotCapture();
        };

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
        this.tabs = [];
        this.activeTab = null;
    }

    async init() {
        document.getElementById('btn-new-tab').addEventListener('click', () => this.createTab());
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
        const response = await fetch('/api/sessions', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({profile: 'default'})
        });
        const data = await response.json();

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

        const sessionIds = scope === 'current' && this.activeTab ? [this.activeTab.uuid] : [];

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

        const data = await response.json();
        this.renderSearchResults(data.results);
    }

    renderSearchResults(results) {
        const container = document.getElementById('search-results');
        container.innerHTML = results.map(r => `
            <div class="search-result">
                <div class="result-header">
                    <span class="session-name">${r.session_name}</span>
                    <span class="timestamp">${r.timestamp}</span>
                </div>
                <pre class="result-content">${r.content.substring(0, 200)}...</pre>
            </div>
        `).join('');
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
