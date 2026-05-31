#!/bin/bash
# Fitbit Dashboard — idempotent launcher
# Usage: bash run.sh

set -e
cd "$(dirname "$0")"
PORT=8080

echo "=== Fitbit Dashboard ==="

# Kill old process on port
OLD_PID=$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
if [ -n "$OLD_PID" ]; then
    echo "Killing old server on port $PORT (PID $OLD_PID)..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
fi

# Create venv if missing
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install deps
source venv/bin/activate
if [ requirements.txt -nt venv/.deps_installed ] 2>/dev/null || [ ! -f venv/.deps_installed ]; then
    echo "Installing dependencies..."
    pip install -q -r requirements.txt
    touch venv/.deps_installed
fi

# Create config template if missing
if [ ! -f config.yaml ]; then
    echo "Creating config.yaml template..."
    cat > config.yaml << 'EOF'
google_client_id: ""
google_client_secret: ""
redirect_uri: "http://localhost:8080/oauth/callback"
host: "0.0.0.0"
port: 8080
EOF
    echo "⚠ Edit config.yaml with your Google OAuth credentials, then re-run."
    exit 0
fi

# Start server
echo "Starting server on http://0.0.0.0:$PORT ..."
PYTHONUNBUFFERED=1 python3 -u server.py
