#!/usr/bin/env bash
set -eux   # echo each command, exit on any error

if [ $# -lt 1 ]; then
  echo "Usage: $0 <node_count> [base_port]"
  exit 1
fi

NODE_COUNT=$1
BASE_PORT=${2:-50000}
LOG_DIR="dht_logs"

# 1) ensure log dir exists
mkdir -p "$LOG_DIR"

# 2) launch each node, backgrounded, with explicit stdout/stderr redirection
for ((i=0; i<NODE_COUNT; i++)); do
  PORT=$((BASE_PORT + i))
  echo ">>> Starting node #$i on 127.0.0.1:$PORT"
  python3 dht.py \
    --host 127.0.0.1 \
    --port "$PORT" \
    --bootstrap \
    --test \
    >"$LOG_DIR/node_${PORT}.log" 2>&1 &
  PID=$!
  echo "    PID=$PID, logging to $LOG_DIR/node_${PORT}.log"
done

echo
echo "All $NODE_COUNT nodes launched."
echo "You can monitor their output with, for example:"
echo "  tail -F $LOG_DIR/node_*.log"
