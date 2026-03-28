To list all panes across all tmux sessions and windows and capture their contents, you can combine `tmux list-panes` with `tmux capture-pane` in a loop. Here's how:

## 1. List all panes with their targets
Use `tmux list-panes -a` to list every pane in all sessions. The `-a` flag includes all sessions. Add a custom format to get a target string that `capture-pane` can use.

```bash
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}'
```

This prints lines like `mysession:1.0` (session `mysession`, window index 1, pane index 0).

## 2. Capture each pane's content
For each target, run `tmux capture-pane -t <target> -p`. The `-p` option prints the captured content to stdout. By default, it captures only the visible pane area; to include scrollback history, add `-S -` (start from the beginning) or `-S -<lines>`.

### Example: Print all pane contents to terminal
```bash
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}' | while read target; do
    echo "===== $target ====="
    tmux capture-pane -t "$target" -p
done
```

### Example: Save each pane's content to a separate file
```bash
tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}' | while read target; do
    filename=$(echo "$target" | tr ':. ' '_').txt  # Replace problematic chars
    tmux capture-pane -t "$target" -p > "$filename"
    echo "Saved $target to $filename"
done
```

### Example: Capture entire history (scrollback) for each pane
Add `-S -` to capture all lines from the beginning of the history buffer:
```bash
tmux capture-pane -t "$target" -p -S -
```

## 3. Using pane IDs (safer with special characters)
If your session names contain colons (`:`) or other characters that break the target format, use pane IDs instead:
```bash
tmux list-panes -a -F '#{pane_id}' | while read pane_id; do
    echo "===== $pane_id ====="
    tmux capture-pane -t "$pane_id" -p
done
```

Pane IDs look like `%0`, `%1`, etc., and are always safe.

## Important notes
- `capture-pane` captures the **visible content** plus any history you specify with `-S`/`-E`. Without options, it captures only the visible area (same as what you see on screen).
- If a pane contains escape sequences (e.g., colors, terminal graphics), they will appear in the captured output. You can strip them with tools like `sed` if needed.
- Large panes with long history can produce huge output. Consider using `-S -<lines>` to limit history, or redirect to files.

These commands can be run directly in your shell (outside tmux) or inside a tmux window.