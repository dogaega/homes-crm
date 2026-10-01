#!/bin/zsh
# Mac runner: one scrape run (full | light), started by launchd (see the plists here).
# Skips if a run is still going; pushes to the Worker, stores nothing but a short log.
mode=${1:-full}
cd "${0:a:h}/../.." || exit 1
lock=/tmp/monaco-scraper.pid
if [[ -f $lock ]] && kill -0 "$(cat $lock)" 2>/dev/null; then exit 0; fi
# The manual refresh started by hand counts as a run too.
pgrep -f "scraper.daily" >/dev/null && exit 0
echo $$ > $lock
trap 'rm -f $lock' EXIT
log=~/Library/Logs/monaco-scraper.log
[[ -f $log && $(stat -f %z $log) -gt 20000000 ]] && mv -f $log $log.1
set -a; . ./.env; set +a
py=/opt/miniconda3/bin/python3
{
  echo "=== $(date '+%F %T') $mode"
  $py -m scraper.daily --mode $mode
  $py -m scraper.daily --mode $mode --runner local
} >> $log 2>&1
