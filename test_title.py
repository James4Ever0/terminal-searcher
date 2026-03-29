#!/usr/bin/env python3
"""
Test script to demonstrate title setting functionality.

This script shows how to:
1. Set terminal titles using escape sequences
2. Test with xtitle command
3. Verify title changes in flashback-terminal
"""

import sys
import time
import subprocess

def test_title_escape_sequences():
    """Test title setting using ANSI escape sequences."""
    print("=== Testing Title Escape Sequences ===")
    print("1. Setting title using ANSI escape sequence...")
    
    # ANSI escape sequence to set window title
    # \x1b]0;Title\x07
    title_cmd = "\x1b]0;Test Title from Escape Sequence\x07"
    print(f"   Sending: {repr(title_cmd)}")
    
    # Test different titles
    titles = [
        "Test Title 1",
        "Terminal Session",
        "My Cool Terminal",
        "Development Environment",
        "bash - user@hostname"
    ]
    
    for i, title in enumerate(titles, 1):
        escape_seq = f"\x1b]0;{title}\x07"
        print(f"   {i}. Setting title to: {title}")
        print(escape_seq, end='')  # This will set the terminal title
        time.sleep(2)

def test_xtitle_command():
    """Test title setting using xtitle command."""
    print("\n=== Testing xtitle Command ===")
    
    # Check if xtitle is available
    try:
        subprocess.run(['which', 'xtitle'], check=True, capture_output=True)
        print("✓ xtitle command found")
    except subprocess.CalledProcessError:
        print("✗ xtitle command not found - install with: sudo apt install xtitle")
        return
    
    # Test xtitle with different titles
    titles = [
        "xtitle Test 1",
        "xtitle Terminal",
        "xtitle Development",
    ]
    
    for i, title in enumerate(titles, 1):
        print(f"   {i}. Setting title with xtitle: {title}")
        try:
            subprocess.run(['xtitle', title], check=True)
            time.sleep(2)
        except subprocess.CalledProcessError as e:
            print(f"   Error: {e}")

def show_instructions():
    """Show instructions for testing with flashback-terminal."""
    print("\n=== Instructions for Testing with flashback-terminal ===")
    print("1. Start flashback-terminal: python -m flashback_terminal")
    print("2. Open web interface: http://localhost:8080")
    print("3. Create a new terminal session")
    print("4. Use the title input field to set custom titles")
    print("5. Or run commands in terminal that set titles:")
    print("   - echo -e '\\x1b]0;My Title\\x07'")
    print("   - xtitle 'My Title'")
    print("6. Observe title changes in:")
    print("   - Tab titles")
    print("   - Window title")
    print("   - Browser tab title")

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == 'escape':
            test_title_escape_sequences()
        elif sys.argv[1] == 'xtitle':
            test_xtitle_command()
        else:
            print("Usage: python test_title.py [escape|xtitle|instructions]")
    else:
        show_instructions()

if __name__ == '__main__':
    main()
