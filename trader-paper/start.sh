#!/bin/sh
# Start the paper trading desk for the day: the dashboard and the engine.
#   sh start.sh                 # from your trading folder (the one holding trader/)
#   sh start.sh /path/to/folder
# The engine runs until 15:35 IST and keeps the Mac awake while it does (caffeinate).
# Logs: trader/paper/engine.log and trader/paper/dashboard.log
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "${1:-.}"
mkdir -p trader/paper
# python.org builds on macOS ship without CA certificates; use the system bundle if Python has none
if [ -z "$SSL_CERT_FILE" ] && [ -f /etc/ssl/cert.pem ]; then export SSL_CERT_FILE=/etc/ssl/cert.pem; fi
if ! curl -s -o /dev/null http://127.0.0.1:8787/api/state; then
  nohup python3 -I "$HERE/dashboard.py" >> trader/paper/dashboard.log 2>&1 &
fi
echo "Dashboard: http://127.0.0.1:8787"
exec caffeinate -i python3 -I "$HERE/paper.py" run >> trader/paper/engine.log 2>&1
