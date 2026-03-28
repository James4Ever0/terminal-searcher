You can capture the contents of a running GNU Screen session using the built-in `hardcopy` command. This will save the currently displayed content and/or scrollback buffer to a file, preserving ANSI escape codes for colors and formatting.

## The `hardcopy` Command

GNU Screen has a built-in `hardcopy` command that dumps the current window's contents to a file:

- **Interactive usage**: Press `Ctrl-a h` (or `Ctrl-a :hardcopy`)
- **Command-line usage**: Send the command to a running session with `screen -X`

By default, `hardcopy` saves only the visible screen area. Use the `-h` flag to include the scrollback buffer.

## Capture to Text File from CLI

Here's a complete one-liner to capture a Screen session's contents to a file, then exit while keeping the session running:

```bash
screen -X hardcopy -h /tmp/screen-capture.txt
```

This command:
- `screen -X` sends a command to a running Screen session (attaches to the default session; use `-S sessionname` to target a specific session)
- `hardcopy -h` dumps both visible and scrollback buffer contents
- `/tmp/screen-capture.txt` is the output file

The session remains running in the background - you're just sending it a command.

## Targeting a Specific Session

If you have multiple Screen sessions, specify which one:

```bash
# Using session name
screen -S mysession -X hardcopy -h /tmp/screen-capture.txt

# Using socket path (from your previous question)
SCREENDIR=/path/to/socketdir screen -S mysession -X hardcopy -h /tmp/screen-capture.txt
```

## Handling ANSI Escape Codes

`hardcopy` preserves ANSI escape codes (colors, formatting) in the output file. If you need plain ASCII text without the escape sequences, strip them after capture:

```bash
# Capture with ANSI codes
screen -X hardcopy -h /tmp/screen-capture.txt

# Remove ANSI codes for plain text version
sed -E 's/\x1b\[[0-9;]*m//g' /tmp/screen-capture.txt > /tmp/screen-capture-plain.txt
```

Alternative method using `col -b`:
```bash
cat /tmp/screen-capture.txt | col -b > /tmp/screen-capture-plain.txt
```

## Complete Script Example

Here's a reusable script that captures, optionally strips codes, and cleans up:

```bash
#!/bin/bash
SESSION="${1:-}"  # First argument: session name (optional)
OUTPUT="${2:-screen-capture.txt}"  # Second argument: output file

if [ -n "$SESSION" ]; then
    screen -S "$SESSION" -X hardcopy -h "$OUTPUT"
else
    screen -X hardcopy -h "$OUTPUT"
fi

# Check if capture succeeded
if [ $? -eq 0 ]; then
    echo "Captured Screen session to $OUTPUT"
    
    # Optionally create plain text version
    sed -E 's/\x1b\[[0-9;]*m//g' "$OUTPUT" > "${OUTPUT%.txt}-plain.txt"
    echo "Plain text version saved to ${OUTPUT%.txt}-plain.txt"
else
    echo "Error: Could not capture Screen session"
    exit 1
fi
```

## Important Notes

- The `hardcopy` command captures the **current window** of the target session
- Files are written with permissions of the user running Screen
- If no filename is specified, it creates `hardcopy.n` in Screen's current working directory
- The capture includes the terminal's raw output including control sequences

This method gives you exactly what you asked for: a command-line capture to text, preserving ANSI codes, with the session continuing uninterrupted in the background.