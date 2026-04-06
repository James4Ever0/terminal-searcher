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

// TODO: when hover on terminal tab title, show details like connection backend (tmux/screen), connection status, last activity time, description, profile name etc.

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
            // forceRender: true
        });

        this.fitAddon = new FitAddon.FitAddon();
        this.terminal.loadAddon(this.fitAddon);

        const container = document.getElementById('terminal-container');
        this.terminal.open(container);
        this.fitAddon.fit();

        const wsUrl = `ws://${window.location.host}/ws/terminal/${this.uuid}`;
        this.socket = new WebSocket(wsUrl);

        this.socket.onopen = () => {
            FrontendLogger.info(`[Terminal ${this.uuid}] WebSocket connected: uuid=${this.uuid}`);
            // this.startScreenshotCapture();
            // resize terminal initially.
            this.fitAddon.fit();
            this.sendResize();
        };

        this.socket.onmessage = (event) => {
            FrontendLogger.trace(`[Terminal ${this.uuid}] WebSocket message received:`, event.data.substring(0, 200));
            const msg = JSON.parse(event.data);
            this.handleMessage(msg);
        };

        this.socket.onclose = async () => {
            FrontendLogger.info(`WebSocket closed: uuid=${this.uuid}`);

            // Check backend health first
            try {
                var isBackendHealthy=false;
                try{
                    const healthResponse = await fetch('/healthcheck');
                    isBackendHealthy = healthResponse.ok;

                } catch {
                    // cannot connect to backend, so the backend is not running. waiting for reconnect.
                    FrontendLogger.info("Healthcheck failed, likely server close.")
                }

                if (!isBackendHealthy) {
                    // Backend is down, likely network issue
                    FrontendLogger.warn('Backend healthcheck failed - possible network issue');
                    // Show reconnect notification when backend is back, run infinite loop for waiting terminal back on line
                    this?.app?.waitForBackendAndReload();
                    return;
                }

                // Check if session is still running in backend
                try {
                    const sessionResponse = await fetch(`/api/sessions/${this.uuid}`);
                    if (sessionResponse.ok) {
                        const sessionData = await sessionResponse.json();
                        if (sessionData.is_running) {
                            // Session is still running, allow reconnection
                            FrontendLogger.info('Session still running in backend - keeping tab available');
                            // TODO: Show reconnect button/notification
                        } else {
                            // Session is not running, close tab
                            FrontendLogger.info('Session no longer running in backend - closing tab');
                            this?.app?.closeTab(this);
                        }
                    } else {
                        FrontendLogger.warn('Failed to check session status - closing tab');
                        this?.app?.closeTab(this);
                    }
                } catch (sessionError) {
                    FrontendLogger.error('Error checking session status:', sessionError);
                    this?.app?.closeTab(this);
                }
            } catch (healthError) {
                FrontendLogger.error('Error during healthcheck:', healthError);
                this?.app?.closeTab(this);
            }
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
                // console.dir(this.terminal);
                break;
            case 'history_replay':
                for (const chunk of msg.chunks) {
                    this.terminal.write(chunk.content);
                }
                break;
            case "cursor":
                this.terminal.write("\x1b[" + (msg.row) + ";" + (msg.col + 1) + "H");
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
                // log session info
                console.log(`[WebSocket] Session info: uuid=${this.uuid}, name=${this.name}`);
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
        // this.stopScreenshotCapture();
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
        this.STORAGE_KEY = 'flashback-terminal-tabs';
        this.previewTimeout = null;
        this.currentPreviewTab = null;
        this.draggedTab = null;
    }

    saveTabState() {
        const tabState = {
            tabs: this.tabs.map(tab => ({
                uuid: tab.uuid,
                name: tab.name,
                originalName: tab.originalName,
                titleOverride: tab.titleOverride
            })),
            activeTabUuid: this.activeTab ? this.activeTab.uuid : null,
            timestamp: new Date().toISOString()
        };
        localStorage.setItem(this.STORAGE_KEY, JSON.stringify(tabState));
        FrontendLogger.debug(`Tab state saved: ${this.tabs.length} tabs`);
    }

    getSavedTabState() {
        try {
            const saved = localStorage.getItem(this.STORAGE_KEY);
            if (saved) {
                const tabState = JSON.parse(saved);
                FrontendLogger.debug(`Found saved tab state: ${tabState.tabs.length} tabs from ${tabState.timestamp}`);
                return tabState;
            }
        } catch (error) {
            FrontendLogger.warn('Failed to parse saved tab state:', error);
        }
        return null;
    }

    clearSavedTabState() {
        localStorage.removeItem(this.STORAGE_KEY);
        FrontendLogger.debug('Saved tab state cleared');
    }

    async restoreTabs() {
        const savedState = this.getSavedTabState();
        if (!savedState || !savedState.tabs.length) {
            FrontendLogger.info('No saved tabs to restore');
            return;
        }

        FrontendLogger.info(`Restoring ${savedState.tabs.length} tabs from saved state`);


        // Create a map of UUID to target position from saved state
        const uuidToTargetIndex = new Map();
        savedState.tabs.forEach((savedTab, index) => {
            uuidToTargetIndex.set(savedTab.uuid, index);
        });

        // console.log("UUID to target index:");
        // console.dir(uuidToTargetIndex);

        const restoredTabs = [];
        let activeTabRestored = null;

        try {
            // Show progress overlay
            this.showRestoreProgress(savedState.tabs.length);

            // Create restoration tasks for all tabs
            let completedCount = 0;
            const restorationTasks = savedState.tabs.map(async (savedTab, index) => {
                try {
                    // Try to attach to existing session
                    const response = await fetch(`/api/sessions/${savedTab.uuid}/attach`, {
                        method: 'POST'
                    });

                    if (response.ok) {
                        const result = await response.json();
                        FrontendLogger.info(`Successfully restored tab: ${savedTab.name} (${savedTab.uuid})`);

                        // Create tab instance
                        const tab = new TerminalTab(result.uuid, result.name);
                        tab.originalName = savedTab.originalName;
                        tab.titleOverride = savedTab.titleOverride;
                        tab.app = this;

                        this.tabs.push(tab);
                        await tab.connect();

                        // Update progress when this tab completes
                        completedCount++;
                        this.updateRestoreProgress(completedCount, savedState.tabs.length);

                        // Set as active tab if it was the active one
                        if (savedTab.uuid === savedState.activeTabUuid) {
                            return { tab, isActive: true };
                        }
                        return { tab, isActive: false };
                    } else {
                        FrontendLogger.warn(`Failed to restore tab: ${savedTab.name} (${savedTab.uuid}) - session may not be available`);
                        // Update progress even for failed tabs
                        completedCount++;
                        this.updateRestoreProgress(completedCount, savedState.tabs.length);
                        return null;
                    }
                } catch (error) {
                    FrontendLogger.error(`Error restoring tab ${savedTab.name}:`, error);
                    // Update progress even for errored tabs
                    completedCount++;
                    this.updateRestoreProgress(completedCount, savedState.tabs.length);
                    return null;
                }
            });

            // Execute all restoration tasks concurrently
            const results = await Promise.all(restorationTasks);

            // Reorder tabs according to saved state order
            this.reorderTabsBySavedState(uuidToTargetIndex);

            // Process results
            const restoredTabs = [];
            let activeTabRestored = null;

            for (const result of results) {
                if (result) {
                    restoredTabs.push(result.tab);
                    this.switchTab(result.tab);
                    if (result.isActive) {
                        activeTabRestored = result.tab;
                    }
                }
            }

            // Switch to the previously active tab, or the first restored tab
            if (restoredTabs.length > 0) {
                const tabToSwitch = activeTabRestored || restoredTabs[0];
                this.switchTab(tabToSwitch);
                FrontendLogger.info(`Tab restoration complete: ${restoredTabs.length} tabs restored`);
            } else {
                FrontendLogger.warn('No tabs could be restored');
                this.clearSavedTabState(); // Clear invalid state
            }
        } catch (error) {
            FrontendLogger.error('Unexpected error during tab restoration:', error);
        } finally {
            // Ensure progress overlay is hidden no matter what happens
            this.hideRestoreProgress();
            this.renderTabs();
        }
    }

    async init() {
        FrontendLogger.info('App initializing...');
        const exitLog = FrontendLogger.logFunction('App.init');

        // Hide expanded preview modal on initialization
        this.closeExpandedPreview();

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
        document.getElementById('btn-timeline').addEventListener('click', () => this.openTimeline());

        document.querySelectorAll('.close-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.target.closest('.modal').classList.add('hidden');
            });
        });

        document.getElementById('btn-do-search').addEventListener('click', () => this.doSearch());
        
        // Add Enter key event listener to search input
        document.getElementById('search-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                this.doSearch();
            }
        });

        // TODO: only if no tab to attach (all tabs in background are not running), we would create a new one instead. otherwise attach existing ones.
        // await this.createTab();

        // Restore previous tabs if available
        await this.restoreTabs();
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
            this.saveTabState(); // Save state after title change
        } else {
            FrontendLogger.warn('Empty title provided, ignoring');
        }
    }

    async createTab() {
        FrontendLogger.info('Creating new tab...');
        const exitLog = FrontendLogger.logFunction('App.createTab');

        const response = await fetch('/api/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ profile: 'default' })
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
        // Save tab state after creating new tab
        this.saveTabState();
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

        // Save state after switching tabs
        this.saveTabState();
    }

    closeTab(tab) {
        // hide tab preview, prevent glitch
        this.hideTabPreview();

        FrontendLogger.info(`Closing tab: uuid=${tab.uuid}`);

        // Find index of the tab
        const tabIndex = this.tabs.indexOf(tab);
        if (tabIndex === -1) return;

        // Send disconnect message to backend before closing
        if (tab.socket && tab.socket.readyState === WebSocket.OPEN) {
            tab.socket.send(JSON.stringify({
                type: 'disconnect',
                keep_session_alive: true
            }));
        }

        // Dispose the tab (closes WebSocket and terminal, but doesn't terminate backend session)
        tab.dispose();

        // Remove tab from array
        this.tabs.splice(tabIndex, 1);

        // If we closed the active tab, switch to another one
        if (this.activeTab === tab) {
            if (this.tabs.length > 0) {
                // Switch to the tab to the right, or the first tab if we're at the end
                const nextTabIndex = tabIndex < this.tabs.length ? tabIndex : 0;
                this.switchTab(this.tabs[nextTabIndex]);
            } else {
                // No tabs left, clear the active tab
                this.activeTab = null;
                document.getElementById('terminal-container').innerHTML = '';
                document.title = 'flashback-terminal';
            }
        }

        // Re-render tabs
        this.renderTabs();

        // Save state after closing tab
        this.saveTabState();

        FrontendLogger.info(`Tab closed successfully: uuid=${tab.uuid}`);
    }

    handleDragStart(e, tabIndex) {
        // focus on the dragged tab first, then handle all the drag events later.
        const draggedTab = this.tabs[tabIndex];
        this.draggedTab = draggedTab;
        if (draggedTab) {
            // Focus the terminal without re-rendering tabs to avoid disrupting drag operation
            if (this.activeTab) {
                this.activeTab.terminal.element.style.display = 'none';
            }
            this.activeTab = draggedTab;
            draggedTab.terminal.element.style.display = 'block';
            draggedTab.focus();

            // Update window title
            const displayName = draggedTab.titleOverride || draggedTab.name || draggedTab.originalName;
            document.title = `${displayName} - flashback-terminal`;
        }

        FrontendLogger.debug(`Drag started: tab index ${tabIndex}`);
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/html', tabIndex);
        e.target.classList.add('dragging');
        this.draggedTabIndex = tabIndex;
    }

    handleDragOver(e) {
        if (e.preventDefault) {
            e.preventDefault();
        }
        e.dataTransfer.dropEffect = 'move';
        return false;
    }

    handleDragEnter(e) {
        if (e.target.classList.contains('tab') && !e.target.classList.contains('dragging')) {
            e.target.classList.add('drag-over');
        }
    }

    handleDragLeave(e) {
        this.draggedTab = null;
        if (e.target.classList.contains('tab')) {
            e.target.classList.remove('drag-over');
        }
    }

    handleDrop(e, dropTabIndex) {
        if (e.stopPropagation) {
            e.stopPropagation();
        }
        e.preventDefault();

        // Remove visual feedback
        document.querySelectorAll('.tab').forEach(tab => {
            tab.classList.remove('drag-over');
        });

        if (this.draggedTabIndex !== undefined && this.draggedTabIndex !== dropTabIndex) {
            // Reorder the tabs array
            const draggedTab = this.tabs[this.draggedTabIndex];
            this.tabs.splice(this.draggedTabIndex, 1);
            this.tabs.splice(dropTabIndex, 0, draggedTab);

            FrontendLogger.info(`Tab reordered: from index ${this.draggedTabIndex} to ${dropTabIndex}`);

            // Re-render tabs to update the order
            this.renderTabs();

            // Save state after reordering tabs
            this.saveTabState();
        }

        return false;
    }

    handleDragEnd(e, tab) {
        // Clean up visual feedback
        document.querySelectorAll('.tab').forEach(tab => {
            tab.classList.remove('dragging');
            tab.classList.remove('drag-over');
        });
        this.draggedTabIndex = undefined;
        this.switchTab(tab);
    }

    showTabPreview(tab, tabElement) {
        // Clear any existing timeout
        if (this.previewTimeout) {
            clearTimeout(this.previewTimeout);
        }

        // Set a small delay before showing preview
        this.previewTimeout = setTimeout(() => {
            // Don't show preview if this is the active tab
            if (tab === this.activeTab) {
                return;
            }

            this.currentPreviewTab = tab;

            // Create or get preview container
            // let previewContainer = document.getElementById('tab-preview');
            // if (!previewContainer) {
            //     previewContainer = document.createElement('div');
            //     previewContainer.id = 'tab-preview';
            //     previewContainer.className = 'tab-preview';
            //     document.body.appendChild(previewContainer);
            // }

            // Force render terminal content even when not focused for tab preview
            // This ensures the preview shows up-to-date terminal content


            if (this.activeTab) {
                this.activeTab.terminal.element.style.display = 'none';
            }

            if (tab.terminal){
                tab.terminal.element.style.display = "block";
                tab.terminal.focus();
            }

            // if (tab.terminal){
            //     let _element = tab.terminal.element;
            //     let _element_display = _element.style.display;
            //     _element.style.display = '';
            //     _element.offsetHeight;
            //     _element.style.display = 'none';
            //     _element.offsetHeight;
            //     _element.style.display = _element_display;
            // }
            // Clone the terminal content for preview
            // const terminalElement = tab.terminal.element;
            // const previewContent = terminalElement.cloneNode(true);
            // previewContent.style.display = 'block';
            // previewContent.style.position = 'static';
            // previewContent.style.height = '400px';
            // previewContent.style.overflow = 'hidden';

            // // Clear preview container and add content
            // previewContainer.innerHTML = '';
            // previewContainer.appendChild(previewContent);

            // // Position preview relative to the terminal container
            // const terminalContainer = document.getElementById('terminal-container');
            // const containerRect = terminalContainer.getBoundingClientRect();

            // previewContainer.style.position = 'fixed';
            // previewContainer.style.top = `${containerRect.top + 20}px`;
            // previewContainer.style.left = `${containerRect.left + 20}px`;
            // previewContainer.style.width = `${containerRect.width - 40}px`;
            // previewContainer.style.zIndex = '1000';
            // previewContainer.style.display = 'block';

            FrontendLogger.debug(`Showing preview for tab: ${tab.name}`);

            // scroll to xterm-cursor.
            // this.scrollToLastHighlight(previewContainer, "xterm-cursor");
        }, 500); // 500ms delay
    }

    hideTabPreview() {
        // Clear any pending preview
        if (this.previewTimeout) {
            clearTimeout(this.previewTimeout);
            this.previewTimeout = null;
        }

        if (this.activeTab) {
            this.activeTab.terminal.element.style.display = 'block';
        }

        if (this.currentPreviewTab){
            if (this.draggedTab){
                FrontendLogger.info("Skip hiding preview tab since it is being dragged");
            }else{
                this.currentPreviewTab.terminal.focus();
                this.currentPreviewTab.terminal.element.style.display = "none";
            }
        }

        // Hide existing preview
        // const previewContainer = document.getElementById('tab-preview');
        // if (previewContainer) {
        //     previewContainer.style.display = 'none';
        // }

        this.currentPreviewTab = null;
        FrontendLogger.debug('Hiding tab preview');
    }

    renderTabs() {
        const container = document.getElementById('tabs');
        container.innerHTML = ''; // clear all tabs? could be inefficient?

        for (let i = 0; i < this.tabs.length; i++) {
            const tab = this.tabs[i];
            const tabEl = document.createElement('div');
            tabEl.className = 'tab' + (tab === this.activeTab ? ' active' : '');
            tabEl.draggable = true;
            tabEl.dataset.tabIndex = i;

            const displayName = tab.titleOverride || tab.name || tab.originalName;

            // Create tab content container
            // too small for click event?
            const tabContent = document.createElement('span');
            tabContent.className = 'tab-content';
            tabContent.textContent = displayName;
            tabContent.title = `Session: ${tab.originalName}\nCurrent: ${displayName}\nUUID: ${tab.uuid}`;

            // tabContent.addEventListener('click', () => this.switchTab(tab));

            // Create close button
            const closeBtn = document.createElement('span');
            closeBtn.className = 'tab-close';
            closeBtn.textContent = '×';
            closeBtn.title = 'Close terminal window (background session continues)';
            closeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.closeTab(tab);
            });

            tabEl.addEventListener('click', () => this.switchTab(tab));

            // Add drag event listeners
            tabEl.addEventListener('dragstart', (e) => this.handleDragStart(e, i));
            tabEl.addEventListener('dragover', (e) => this.handleDragOver(e));
            tabEl.addEventListener('drop', (e) => this.handleDrop(e, i));
            tabEl.addEventListener('dragend', (e) => this.handleDragEnd(e, tab));
            tabEl.addEventListener('dragenter', (e) => this.handleDragEnter(e));
            tabEl.addEventListener('dragleave', (e) => this.handleDragLeave(e));

            // Add hover preview event listeners
            tabEl.addEventListener('mouseenter', (e) => this.showTabPreview(tab, tabEl));
            tabEl.addEventListener('mouseleave', () => this.hideTabPreview());

            tabEl.appendChild(tabContent);
            tabEl.appendChild(closeBtn);
            container.appendChild(tabEl);
        }
    }

    openSearch() {
        // Hide any expanded preview when opening search
        this.closeExpandedPreview();
        
        // Reset search form to pristine state
        document.getElementById('search-input').value = '';
        document.getElementById('search-results').innerHTML = '';
        document.getElementById('search-mode').value = 'text';
        document.getElementById('search-scope').value = 'current';
        
        // Show the modal
        document.getElementById('search-modal').classList.remove('hidden');
        
        // Add escape key listener
        document.addEventListener('keydown', this.handleEscapeKeyForSearch);
    }

    async doSearch() {
        const query = document.getElementById('search-input').value;
        const mode = document.getElementById('search-mode').value;
        const scope = document.getElementById('search-scope').value;

        FrontendLogger.info(`Search initiated: query="${query}", mode=${mode}, scope=${scope}`);
        const exitLog = FrontendLogger.logFunction('App.doSearch', { query, mode, scope });

        if (scope === 'current') {
            if (this.activeTab) {
                FrontendLogger.info("Searching in current tab");
            } else {
                FrontendLogger.warn("No active tab to search in");
                // alert user
                alert("No active tab to search in");
                return;
            }
        }

        // Show searching feedback
        this.renderSearchStatus('Searching...');

        const sessionIds = scope === 'current' && this.activeTab ? [this.activeTab.uuid] : [];

        console.log("[doSearch] sessionIds:", sessionIds);

        try {
            const response = await fetch('/api/search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
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

            const allSessionsReponse = await fetch('/api/sessions');
            const allSessionsData = await allSessionsReponse.json();

            this.renderSearchResults(data.results, allSessionsData);
        } catch (error) {
            console.log("Search failed:", error);
            this.renderSearchStatus('Request failed');
        }
    }

    renderSearchStatus(message) {
        const container = document.getElementById('search-results');
        container.innerHTML = `<div class="search-status">${message}</div>`;
    }

    renderSearchResults(results, allSessionsData) {
        const container = document.getElementById('search-results');

        if (!results || results.length === 0) {
            container.innerHTML = '<div class="search-status">No results found</div>';
            return;
        }

        const countFeedback = `<div class="search-count">${results.length} result${results.length !== 1 ? 's' : ''} found</div>`;

        // Get set of running terminal UUIDs
        const runningUuids = new Set(this.tabs.map(tab => tab.uuid));

        var sessions_map = {};
        for (let it of allSessionsData.sessions) {
            sessions_map[it.uuid] = it;
        }

        console.log("sessions map", sessions_map);

        // Group results by session UUID and title
        const groupedResults = {};
        results.forEach(r => {
            const sessionKey = `${r.session_uuid}|${r.session_name}`;
            if (!groupedResults[sessionKey]) {
                groupedResults[sessionKey] = {
                    session_uuid: r.session_uuid,
                    session_name: r.session_name,
                    session_type: r.session_type,
                    results: []
                };
            }
            groupedResults[sessionKey].results.push(r);
        });

        // Store search query for highlighting
        this.currentSearchQuery = document.getElementById('search-input').value;

        const resultsHtml = Object.entries(groupedResults).map(([sessionKey, sessionGroup]) => {
            const isRunning = runningUuids.has(sessionGroup.session_uuid);
            const sess = sessions_map[sessionGroup.session_uuid];

            var buttonClass = isRunning ? 'jump-btn' : 'jump-btn disabled';
            var buttonText = isRunning ? 'Jump to Terminal' : 'Terminal Not Available';
            var onClick = isRunning ? `app.jumpToTerminal('${sessionGroup.session_uuid}')` : '';

            if (!isRunning && sess) {
                if (sess.socket_present) {
                    const existingTab = this.tabs.find(t => t.uuid === sess.uuid);
                    if (existingTab) {
                        buttonClass = 'btn-attach';
                        buttonText = 'Attach';
                        onClick = `app.attachToSession('${sessionGroup.session_uuid}')`;
                    } else {
                        buttonClass = 'btn-attach';
                        buttonText = 'Force Attach';
                        onClick = `app.forceAttachToSession('${sessionGroup.session_uuid}')`;
                    }
                } else {
                    buttonClass = 'btn-restore';
                    buttonText = 'Revive';
                    onClick = `app.reviveSession('${sessionGroup.session_uuid}')`;
                }
            } else if (!isRunning && !sess) {
                buttonClass = 'jump-btn disabled';
                buttonText = "Terminal not available";
                onClick = '';
            }


            // Create timestamp tabs
            const timestampTabs = sessionGroup.results.map((r, index) => {
                const timestamp = new Date(r.timestamp).toLocaleString();
                const encodedContent = encodeURI(r.content);
                return `
                    <div class="timestamp-tab" 
                         onmouseenter="app.showSearchPreview('${r.session_uuid}', '${r.timestamp}', this)"
                         onmouseleave="app.hideSearchPreview()"
                         onclick="app.showExpandedPreview('${r.session_uuid}', '${r.timestamp}', this)"
                         data-timestamp="${r.timestamp}"
                         data-content="${encodedContent}">
                        <span class="timestamp-text">${timestamp}</span>
                        <div class="preview-tooltip" id="preview-${r.session_uuid}-${r.timestamp}"></div>
                    </div>
                `;
            }).join('');

            return `
            <div class="search-result-group">
                <div class="session-header">
                    <div class="session-info">
                        <div class="session-name">${sessionGroup.session_name}</div>
                        <div class="session-uuid">UUID: ${sessionGroup.session_uuid}</div>
                        <div class="session-type">Mode: ${sessionGroup.session_type}</div>
                    </div>
                    <button class="${buttonClass}" data-uuid="${sessionGroup.session_uuid}" onclick="${onClick}">
                        ${buttonText}
                    </button>
                </div>
                <div class="timestamp-tabs-container">
                    ${timestampTabs}
                </div>
            </div>
        `;
        }).join('');

        container.innerHTML = countFeedback + resultsHtml;
    }

    showSearchPreview(sessionUuid, timestamp, element) {
        const tooltip = document.getElementById(`preview-${sessionUuid}-${timestamp}`);
        const encodedContent = element.getAttribute('data-content');
        
        if (tooltip && encodedContent) {
            // Decode the content and create preview element
            const content = decodeURI(encodedContent);
            const previewContent = document.createElement('div');
            previewContent.className = 'preview-content';
            // no truncation or we cannot scroll to highlight.
            // previewContent.textContent = content.substring(0, 300);
            previewContent.textContent = content
            
            // Clear tooltip and add content
            tooltip.innerHTML = '';
            tooltip.appendChild(previewContent);
            
            // Use mark.js to highlight keywords
            const encodedQuery = encodeURI(this.currentSearchQuery.trim());
            const query = decodeURI(encodedQuery);
            if (query) {
                const markInstance = new Mark(previewContent);
                markInstance.mark(query, {
                    className: 'search-highlight',
                    caseSensitive: false,
                    exclude: ['script', 'style', 'title', 'head', 'html']
                });
            }
            
            tooltip.style.display = 'block';
            
            // Position tooltip with directional logic
            const rect = element.getBoundingClientRect();
            const tooltipRect = tooltip.getBoundingClientRect();
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;
            
            // Determine vertical position (top vs bottom)
            const isInTopHalf = rect.top < viewportHeight / 2;
            let topPosition;
            
            if (isInTopHalf) {
                // Show below the element (current behavior)
                topPosition = rect.bottom + 5;
            } else {
                // Show above the element
                topPosition = rect.top - tooltipRect.height - 5;
            }
            
            // Determine horizontal position (left vs right)
            const isInLeftHalf = rect.left < viewportWidth / 2;
            let leftPosition;
            
            if (isInLeftHalf) {
                // Align with left edge of element (current behavior)
                leftPosition = rect.left;
            } else {
                // Align with right edge of element
                leftPosition = rect.right - tooltipRect.width;
            }
            
            tooltip.style.left = `${leftPosition}px`;
            tooltip.style.top = `${topPosition}px`;

            // this.scrollToFirstHighlight(tooltip, 'search-highlight');
            this.scrollToLastHighlight(tooltip, 'search-highlight');
        }
    }

    showExpandedPreview(sessionUuid, timestamp, element) {
        const modal = document.getElementById('expanded-preview-modal');
        const previewBody = document.getElementById('expanded-preview-body');
        const encodedContent = element.getAttribute('data-content');
        
        if (modal && previewBody && encodedContent) {
            // Decode the content
            const content = decodeURI(encodedContent);
            
            // Create preview element
            const previewContent = document.createElement('div');
            previewContent.className = 'expanded-preview-text';
            previewContent.textContent = content;
            
            // Clear and set content
            previewBody.innerHTML = '';
            previewBody.appendChild(previewContent);
            
            // Use mark.js to highlight keywords
            const encodedQuery = encodeURI(this.currentSearchQuery.trim());
            const query = decodeURI(encodedQuery);
            if (query) {
                const markInstance = new Mark(previewContent);
                markInstance.mark(query, {
                    className: 'search-highlight',
                    caseSensitive: false,
                    exclude: ['script', 'style', 'title', 'head', 'html']
                });
            }
            
            // Show modal
            modal.classList.remove('hidden');
            
            // Add escape key listener
            document.addEventListener('keydown', this.handleEscapeKey);
            // this.scrollToFirstHighlight(modal, 'search-highlight');
            this.scrollToLastHighlight(modal, 'search-highlight');
        }
    }
    
    closeExpandedPreview() {
        const modal = document.getElementById('expanded-preview-modal');
        if (modal) {
            modal.classList.add('hidden');
            // Remove escape key listener
            document.removeEventListener('keydown', this.handleEscapeKey);
        }
    }
    

    handleEscapeKeyForSearch = (event) => {
        if (event.key === 'Escape') {
            // Check if expanded preview is open first (higher priority)
            const expandedModal = document.getElementById('expanded-preview-modal');
            if (expandedModal && !expandedModal.classList.contains('hidden')) {
                this.closeExpandedPreview();
                event.stopPropagation();
                return; // Don't close search modal if expanded preview was open
            }
            // Only close search modal if expanded preview is not open
            this.closeSearchModal();
            event.stopPropagation();
            document.removeEventListener("keydown", this.handleEscapeKeyForSearch);
        }
    }

    handleEscapeKey = (event) => {
        if (event.key === 'Escape') {
            this.closeExpandedPreview();
        }
        // stop event propagation
        event.stopPropagation();
    }

    // more useful than scrollToFirstHighlight.
    scrollToLastHighlight(container, className) {
        const searchHighlights = container.getElementsByClassName(className);
        // console.log("search highlight selected elements:", firstHighlight)
        if (searchHighlights.length !== 0) {
            // Scroll the highlighted element into view
            const lastHighlight = searchHighlights[searchHighlights.length - 1]
            lastHighlight.scrollIntoView({
                behavior: 'smooth',
                block: 'center',
                inline: 'nearest'
            });
        }
    }

    scrollToFirstHighlight(container, className) {
        const firstHighlight = container.querySelector('.'+className);
        console.log("search highlight selected elements:", firstHighlight)
        if (firstHighlight) {
            // Scroll the highlighted element into view
            // console.log("focusing the first highlight element");
            firstHighlight.scrollIntoView({
                behavior: 'smooth',
                block: 'center',
                inline: 'nearest'
            });
        }
    }

    hideSearchPreview() {
        document.querySelectorAll('.preview-tooltip').forEach(tooltip => {
            tooltip.style.display = 'none';
        });
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    jumpToTerminal(uuid) {
        const tab = this.tabs.find(t => t.uuid === uuid);
        if (tab) {
            this.switchTab(tab);
            // Close search modal
            this.closeSearchModal();
        }
    }

    closeSearchModal() {
        // Hide expanded preview when closing search modal
        this.closeExpandedPreview();
        
        if (!document.getElementById('search-modal').classList.contains('hidden')) {
            document.getElementById('search-modal').classList.add('hidden');
            // Remove escape key listener
            document.removeEventListener('keydown', this.handleEscapeKey);
        }
    }

    async openSessions() {
        document.getElementById('sessions-modal').classList.remove('hidden');
        await this.loadSessions();
    }

    openTimeline(options) {
        let url = "/timeline";
        let uuid = options? options.uuid : null;
        let timestamp = options? options.timestamp: null;

        let query_params = [];

        if (uuid) {
            query_params.push("uuid="+uuid);
        }

        if (timestamp) {
            query_params.push("timestamp="+timestamp);
        }

        if (query_params.length > 0){
            url += "?" + query_params.join("&")
        }

        window.open(url, '_blank');
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

            // how do you know it is "running"? has websocket connection? or "is_attached"?
            // logic is unclear. to be refactored.
            // if the socket is gone, then it must not be running.
            // check it in backend.
            if (s.is_running) {
                // Check if we have this tab?
                const existingTab = this.tabs.find(t => t.uuid === s.uuid);
                if (existingTab) {
                    // Session is already running, can switch to it
                    actionButtons = `
                    <button class="btn-switch" onclick="app.switchToSession('${s.uuid}')">Switch To</button>
                `;
                }
                else {
                    // Session is running but we don't have a tab for it
                    actionButtons = `
                    <button class="btn-attach" onclick="app.forceSwitchToSession('${s.uuid}')">Force Attach</button>
                `;
                }
            } else if (s.socket_present) {
                // Session can be attached/restored
                actionButtons = `
                    <button class="btn-attach" onclick="app.attachToSession('${s.uuid}')">Attach</button>
                `;
            } else {
                // Session is not available for attachment
                // BUT we might want to restore?
                // i mean that restore thing shall be "clone"

                actionButtons = `<button class="btn-restore" onclick="app.reviveSession('${s.uuid}')">Revive</button>`;

                // actionButtons = `
                //     <span class="status-unavailable">Unavailable</span>
                // `;
            }

            return `
                <div class="session-item">
                    <div class="session-info">
                        <div class="session-name">${s.name}</div>
                        <div class="session-details">
                            <div class="session-uuid">UUID: ${s.uuid}</div>
                            <div class="session-mode">Mode: ${s.session_type}</div>
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


    async forceSwitchToSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.forceSwitchToSession');

        // Check if we already have a tab for this session
        const existingTab = this.tabs.find(t => t.uuid === sessionUuid);
        if (existingTab) {
            this.switchTab(existingTab);
            this.closeSessionsModal();
            this.closeSearchModal();
            return;
        }

        await this.forceAttachToSession(sessionUuid);
        // this.closeSessionsModal();
    }

    async switchToSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.switchToSession');

        // Check if we already have a tab for this session
        const existingTab = this.tabs.find(t => t.uuid === sessionUuid);
        if (existingTab) {
            this.switchTab(existingTab);
            this.closeSessionsModal();
            this.closeSearchModal();
            return;
        }

        await this.attachToSession(sessionUuid);
        // this.closeSessionsModal();
    }


    async forceAttachToSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.forceAttachToSession');

        try {
            this.showLoading(`Force attaching to session ${sessionUuid}...`);

            const response = await fetch(`/api/sessions/${sessionUuid}/force-attach`, {
                method: 'POST'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to force attach to session');
            }

            const result = await response.json();
            console.log('Force attached to session:', result);

            // Create a new tab for the attached session
            const tab = new TerminalTab(result.uuid, result.name);
            tab.app = this;
            this.tabs.push(tab);
            await tab.connect();

            this.switchTab(tab);
            this.renderTabs();

            this.closeSessionsModal();
            this.closeSearchModal();
            this.hideLoading();

            // Save state after attaching to session
            this.saveTabState();

        } catch (error) {
            console.error('Failed to force attach to session:', error);
            this.showError(`Failed to force attach to session: ${error.message}`);
            this.hideLoading();
        }
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
            tab.app = this;
            this.tabs.push(tab);
            await tab.connect();

            this.switchTab(tab);
            this.renderTabs();

            this.closeSessionsModal();
            this.closeSearchModal();
            this.hideLoading();

            // Save state after attaching to session
            this.saveTabState();

        } catch (error) {
            console.error('Failed to attach to session:', error);
            this.showError(`Failed to attach to session: ${error.message}`);
            this.hideLoading();
        }
    }

    async reviveSession(sessionUuid) {
        const exitLog = FrontendLogger.logFunction('App.reviveSession');

        try {
            this.showLoading(`Reviving session ${sessionUuid}...`);

            const response = await fetch(`/api/sessions/${sessionUuid}/revive`, {
                method: 'POST'
            });

            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Failed to revive session');
            }

            const result = await response.json();
            console.log('Revived and attached to session:', result);

            // Create a new tab for the attached session
            const tab = new TerminalTab(result.uuid, result.name);
            tab.app = this;
            this.tabs.push(tab);
            await tab.connect();

            this.switchTab(tab);
            this.renderTabs();

            this.closeSessionsModal();
            this.closeSearchModal();
            this.hideLoading();

            // Save state after restoring session
            this.saveTabState();
        } catch (error) {
            console.error('Failed to revive session:', error);
            this.showError(`Failed to revive session: ${error.message}`);
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
            tab.app = this;
            this.tabs.push(tab);
            await tab.connect();

            this.switchTab(tab);
            this.renderTabs();

            this.closeSessionsModal();
            this.closeSearchModal();
            this.hideLoading();

            // Save state after restoring session
            this.saveTabState();
        } catch (error) {
            console.error('Failed to restore session:', error);
            this.showError(`Failed to restore session: ${error.message}`);
            this.hideLoading();
        }
    }

    closeSessionsModal() {
        if (!document.getElementById('sessions-modal').classList.contains('hidden')) {
            document.getElementById('sessions-modal').classList.add('hidden');
        }
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

    showReconnectNotification() {
        // Check if reconnect modal already exists and is visible
        const reconnectModal = document.getElementById('reconnect-modal');
        if (reconnectModal && !reconnectModal.classList.contains('hidden')) {
            return; // Already showing
        }

        // Show reconnect modal
        if (reconnectModal) {
            reconnectModal.classList.remove('hidden');
            FrontendLogger.info('Showing reconnect notification');
        }
    }

    hideReconnectNotification() {
        const reconnectModal = document.getElementById('reconnect-modal');
        if (reconnectModal) {
            reconnectModal.classList.add('hidden');
            FrontendLogger.info('Hiding reconnect notification');
        }
    }

    async waitForBackendAndReload() {
        FrontendLogger.info('Starting to wait for backend to come back online');
        
        const checkBackend = async () => {
            try {
                const response = await fetch('/healthcheck');
                if (response.ok) {
                    FrontendLogger.info('Backend is back online - reloading page');
                    this.hideReconnectNotification();
                    window.location.reload();
                    return true;
                }
            } catch (error) {
                // Backend still not available
            }
            return false;
        };

        // Check immediately first
        if (await checkBackend()) {
            return;
        }

        // Show reconnect notification
        this.showReconnectNotification();

        // Then check every 2 seconds
        while (true) {
            await new Promise(resolve => setTimeout(resolve, 2000));
            if (await checkBackend()) {
                break;
            }
        }
    }

    showRestoreProgress(total) {
        const overlay = document.getElementById('restore-progress-overlay');
        const currentSpan = document.getElementById('restore-current');
        const totalSpan = document.getElementById('restore-total');
        const progressFill = document.getElementById('restore-progress-fill');
        
        if (overlay && currentSpan && totalSpan && progressFill) {
            currentSpan.textContent = '0';
            totalSpan.textContent = total;
            progressFill.style.width = '0%';
            overlay.classList.remove('hidden');
            FrontendLogger.info(`Showing restore progress for ${total} tabs`);
        }
    }

    updateRestoreProgress(current, total) {
        const currentSpan = document.getElementById('restore-current');
        const progressFill = document.getElementById('restore-progress-fill');
        
        if (currentSpan && progressFill) {
            currentSpan.textContent = current;
            const percentage = total > 0 ? (current / total) * 100 : 0;
            progressFill.style.width = `${percentage}%`;
        }
    }

    hideRestoreProgress() {
        const overlay = document.getElementById('restore-progress-overlay');
        if (overlay) {
            overlay.classList.add('hidden');
            FrontendLogger.info('Hiding restore progress overlay');
        }
    }

    reorderTabsBySavedState(uuidToTargetIndex) {
        // Sort current tabs by their saved order
        this.tabs.sort((a, b) => {
            // if the value is zero, the logic shortcut would get fucked. so we plus one.
            const aIndex = (uuidToTargetIndex.get(a.uuid)+1) || Number.MAX_SAFE_INTEGER;
            const bIndex = (uuidToTargetIndex.get(b.uuid)+1) || Number.MAX_SAFE_INTEGER;
            console.log("aIndex:", aIndex, "bIndex:", bIndex, "aUUID:", a.uuid, 
                "bUUID", b.uuid
            )
            return (aIndex-bIndex);
        });

        FrontendLogger.info(`Tabs reordered according to saved state: ${this.tabs.map(t => t.uuid).join(', ')}`);
    }
}

const app = new App();
app.init();
