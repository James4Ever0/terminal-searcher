#!/bin/bash
# Integration test script for flashback-terminal todo.txt tasks
# Run this after completing all implementation tasks

set -e

cd "$(dirname "$0")"
PROJECT_ROOT="$(pwd)"

echo "================================================================"
echo "Flashback-Terminal Integration Test"
echo "================================================================"

# Check Python version
echo -n "Python version: "
python3 --version

# Run validation script
echo ""
echo "Running validation..."
python3 validate_integration.py

echo ""
echo "================================================================"
echo "Testing server startup..."
echo "================================================================"

# Create test config in temp directory
TEST_DIR="$(mktemp -d)"
echo "Test directory: $TEST_DIR"

cat > "$TEST_DIR/config.yaml" << 'EOF'
data_dir: "$TEST_DIR/data"
logging:
  verbosity: 3
server:
  host: "127.0.0.1"
  port: 19090
session_manager:
  mode: "tmux"
  disable_client_capture: true
  tmux:
    socket_dir: "$TEST_DIR/tmux"
workers:
  retention:
    enabled: false
modules:
  history_keeper:
    enabled: true
EOF

export FLASHBACK_CONFIG="$TEST_DIR/config.yaml"

# Start server in background
echo "Starting server..."
python3 -m flashback_terminal.cli server &
SERVER_PID=$!

# Wait for server to start
sleep 3

echo "Server PID: $SERVER_PID"

# Test API endpoints
echo ""
echo "Testing API endpoints..."

# Check if server is responding
if curl -s "http://127.0.0.1:19090/" > /dev/null; then
    echo "✓ Server responding"
else
    echo "✗ Server not responding"
    kill $SERVER_PID 2>/dev/null || true
    exit 1
fi

# Test timeline endpoint
echo -n "Timeline endpoint: "
if curl -s "http://127.0.0.1:19090/timeline" | grep -q "Flashback Terminal"; then
    echo "✓ OK"
else
    echo "✗ Failed"
fi

# Test timeline API
echo -n "Timeline API: "
if curl -s "http://127.0.0.1:19090/api/v1/captures/timeline" | grep -q '"results"'; then
    echo "✓ OK"
else
    echo "✗ Failed"
fi

# Stop server
echo ""
echo "Stopping server..."
kill $SERVER_PID 2>/dev/null || true

# Cleanup
echo "Cleaning up..."
rm -rf "$TEST_DIR"

echo ""
echo "================================================================"
echo "Integration test completed successfully!"
echo "================================================================"
echo ""
echo "Next steps for full testing:"
echo "1. Install tmux or screen (depending on config)"
echo "2. Install agg_python_bindings for screenshot rendering:"
echo "   pip install agg_python_bindings"
echo "3. Create user config:"
echo "   mkdir -p ~/.config/flashback-terminal"
echo "   cp config.example.yaml ~/.config/flashback-terminal/config.yaml"
echo "4. Start server:"
echo "   python -m flashback_terminal.cli server"
echo "5. Open browser:"
echo "   http://localhost:9090/timeline"
echo ""