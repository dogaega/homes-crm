#!/bin/zsh
# Outreach jobs on the Mac (launchd): plan (morning), send (every 5 min), replies (hourly).
cd /Users/markmirimsky/Projects/monaco-riviera-crm-app/local-pipeline
set -a; . ./.env; set +a; export PYTHONUNBUFFERED=1
P=/opt/miniconda3/bin/python3
log=~/Library/Logs/monaco-outreach.log
case "$1" in
  plan)
    # the sheet first, then today's matches → queue (one planner at a time)
    pgrep -f "outreach.daily" >/dev/null && exit 0
    $P -c "import sys; sys.path.insert(0,'.'); from sync import netwait; from sync.worker_client import WorkerClient; print(WorkerClient(timeout=180).request('POST','/sync/requests-sheet',{}))" >> $log 2>&1
    $P -m outreach.daily >> $log 2>&1 ;;
  send)    $P -m outreach.send --once >> $log 2>&1 ;;
  replies) pgrep -f "outreach.replies" >/dev/null && exit 0; $P -m outreach.replies >> $log 2>&1 ;;
esac
