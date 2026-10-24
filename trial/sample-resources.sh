#!/usr/bin/env bash
# Record the trial containers' CPU and memory every minute to evidence/resources.csv (T5.1).
# Leave it running during a session (or the soak); stop it with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"
out=evidence/resources.csv
mkdir -p evidence
[ -e "$out" ] || echo "utc,container,cpu_percent,memory_usage,memory_percent,pids" >"$out"
echo "Sampling every 60 s into $out (Ctrl+C to stop)"
while true; do
	now="$(date -u +%FT%TZ)"
	docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.PIDs}}' |
		grep '^velora-trial-' | sed "s/^/$now,/" >>"$out" || true
	sleep 60
done
