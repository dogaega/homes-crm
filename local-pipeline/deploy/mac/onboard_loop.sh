#!/bin/zsh
# Onboard sites as the finders produce them; when idle, retry sites that were unreachable.
cd /Users/markmirimsky/Projects/monaco-riviera-crm-app/local-pipeline
set -a; . ./.env; set +a; export PYTHONUNBUFFERED=1
P=/opt/miniconda3/bin/python3
# One copy only: two loops would overwrite each other's site_configs.json saves.
exec 9> /tmp/monaco-onboard.lock
if ! /usr/bin/lockf -t 0 9; then echo "another onboard loop is running"; exit 0; fi
# This Mac has 8 GB: never stack onboarding on top of the daily scrape or on low memory.
calm() {
  while pgrep -f "scraper.daily" >/dev/null || \
        [ "$(memory_pressure | awk '/free percentage/ {gsub("%","",$NF); print $NF}')" -lt 20 ]; do
    sleep 120
  done
}
while true; do
  calm
  out=$($P recon/onboard.py --batch 60 --workers 3 2>&1 | grep -v "^WARNING\|^INFO\|blocked/failed")
  echo "$(date '+%F %T') $(echo "$out" | head -2 | tr '\n' ' ')"
  if echo "$out" | grep -q "onboarding 0 new sites"; then
    g=$($P recon/onboard.py --regate --workers 3 2>&1 | grep -v "^WARNING\|^INFO\|blocked/failed\|internet is"); g=$(echo "$g" | tail -2 | tr '\n' ' ')
    echo "$(date '+%F %T') regate: $g"
    r=$($P recon/onboard.py --retry-unreachable --workers 3 2>&1 | grep -v "^WARNING\|^INFO\|blocked/failed\|internet is"); r=$(echo "$r" | tail -2 | tr '\n' ' ')
    echo "$(date '+%F %T') retry: $r"
    if ! screen -ls | grep -q -E "monaco-(websites|guess|fnaim)" && echo "$r" | grep -q "retrying 0 "; then echo DONE; break; fi
    sleep 600
  fi
done
