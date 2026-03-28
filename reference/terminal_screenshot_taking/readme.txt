i have created a python library named agg-python-bindings==0.1.4 for taking arbitrary screenshot from terminal output.

capture terminal with escape sequence: tmux capture-pane -pe -t <session_name> > <output_file>

capture terminal with text only: tmux capture-pane -p -t <session_name> > <output_file>

capture all visible area with escape sequence: tmux capture-pane -pe -t <session_name> -S - -E - > <output_file>

capture all visible area with text only: tmux capture-pane -p -t <session_name> -S - -E - > <output_file>

get terminal width and height: tmux display -t <session_name> -p '#{pane_width} #{pane_height}'