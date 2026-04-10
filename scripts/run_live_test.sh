#!/bin/bash
# ──────────────────────────────────────────────────────────
# run_live_test.sh — Start backend + frontend for live test
# ──────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "──────────────────────────────────────────"
echo "  CloudSync Live Memory Comparison Test"
echo "──────────────────────────────────────────"

# Load .env if exists
if [ -f .env ]; then
    echo "Loading .env..."
    set -a; source .env; set +a
fi

# Check Python deps
echo "Checking Python dependencies..."
pip install -q fastapi uvicorn pyyaml openai httpx 2>/dev/null || true

# Start backend
echo "Starting backend (port 8000)..."
LIVE_RUN_CONFIG="configs/live_run.yaml" \
    uvicorn runner.live_runner:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Start frontend
echo "Starting frontend (port 5173)..."
cd frontend
npm install --silent 2>/dev/null || true
npm run dev &
FRONTEND_PID=$!
cd "$ROOT"

echo ""
echo "──────────────────────────────────────────"
echo "  Backend:   http://localhost:8000"
echo "  Frontend:  http://localhost:5173"
echo "  WebSocket: ws://localhost:8000/ws"
echo "──────────────────────────────────────────"
echo ""
echo "Press Ctrl+C to stop both servers."

# Trap cleanup
trap "echo 'Shutting down...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM

# Wait
wait
