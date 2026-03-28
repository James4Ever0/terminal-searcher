import agg_python_bindings

source = "scrape_data_session_ansi.bin"

target = "terminal_screenshot.png"

# we do not know how large the terminal is, actually.
# in production maybe we can stick with 80x25

# tmux capture-pane -pe -t scrape_data > scrape_data_session_ansi.bin 
# get from: tmux display -t scrape_data -p '#{pane_width} #{pane_height}'
emulator = agg_python_bindings.TerminalEmulator(125, 14)
emulator.feed_str(open(source, "r").read())
emulator.screenshot(target)