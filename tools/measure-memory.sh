#!/usr/bin/env bash
# Sample the resident memory of the Cinnamon shell process on a running
# Linux desktop. Usage: tools/measure-memory.sh [interval_s] [duration_s] [csv]
set -u

interval="${1:-2}"
duration="${2:-120}"
csv="${3:-cinnamon-rss.csv}"

command -v awk >/dev/null || { echo "awk is required" >&2; exit 1; }

echo "timestamp,cinnamon_rss_kb,process_count" > "$csv"
echo "Sampling Cinnamon RSS every ${interval}s for ${duration}s -> $csv"

end=$(( $(date +%s) + duration ))
while [ "$(date +%s)" -lt "$end" ]; do
    pids=$(pgrep -x cinnamon || true)
    total=0
    count=0
    for pid in $pids; do
        rss=$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status" 2>/dev/null || echo 0)
        total=$(( total + ${rss:-0} ))
        count=$(( count + 1 ))
    done
    printf '%s,%s,%s\n' "$(date -u +%FT%TZ)" "$total" "$count" >> "$csv"
    sleep "$interval"
done

awk -F, 'NR>1 {sum+=$2; n++; if (min=="" || $2<min) min=$2; if ($2>max) max=$2}
         END {if (!n) {print "No samples collected"; exit 1}
              printf "samples=%d min=%d KiB avg=%d KiB max=%d KiB\n", n, min, sum/n, max}' "$csv"
