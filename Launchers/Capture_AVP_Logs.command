#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
REPO_DIR="${SCRIPT_DIR:h}"
LOG_DIR="$REPO_DIR/Logs"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_PATH="${AVP_LOG_PATH:-$LOG_DIR/avp_xcode_$STAMP.ndjson}"
TIMEOUT="${AVP_LOG_TIMEOUT:-20m}"

mkdir -p "$LOG_DIR"

if [[ -n "${AVP_LOG_PROCESS:-}" ]]; then
    PREDICATE="process CONTAINS[c] \"$AVP_LOG_PROCESS\""
else
    PREDICATE="${AVP_LOG_PREDICATE:-process CONTAINS[c] \"Unity\" OR process CONTAINS[c] \"ACCESS\" OR process CONTAINS[c] \"NDI\" OR subsystem CONTAINS[c] \"ndi\" OR eventMessage CONTAINS[c] \"NDI\" OR eventMessage CONTAINS[c] \"audio\" OR eventMessage CONTAINS[c] \"video\"}"
fi

echo "[AVP] Capturing unified logs..."
echo "[AVP] Repo     : $REPO_DIR"
echo "[AVP] Output   : $LOG_PATH"
echo "[AVP] Timeout  : $TIMEOUT"
echo "[AVP] Predicate: $PREDICATE"
echo "[AVP] Press Ctrl-C to stop."

/usr/bin/log stream \
    --style ndjson \
    --level debug \
    --timeout "$TIMEOUT" \
    --predicate "$PREDICATE" | tee "$LOG_PATH"
