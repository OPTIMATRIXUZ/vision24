#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

BACKOFF_START=3
BACKOFF_MAX=60
HEALTHY_AFTER=120  # a run lasting this long counts as good; reset the backoff
MAX_RAPID_FAILURES=8

pkill -f "worker\.main" 2>/dev/null || true
pkill -f "worker\.camera_main" 2>/dev/null || true
sleep 1
trap 'echo; echo "worker stopped"; exit 0' INT TERM
echo "Vision24 detection worker starting (Ctrl-C to stop)"

backoff=$BACKOFF_START
failures=0
while true; do
  started=$SECONDS
  .venv/bin/python -m worker.main "$@"
  code=$?
  ran=$((SECONDS - started))

  [ "$code" -eq 0 ] && break # clean shutdown — don't resurrect

  if [ "$ran" -ge "$HEALTHY_AFTER" ]; then
    backoff=$BACKOFF_START
    failures=0
  else
    failures=$((failures + 1))
  fi

  if [ "$failures" -ge "$MAX_RAPID_FAILURES" ]; then
    echo
    echo "worker failed $failures times in a row without staying up for ${HEALTHY_AFTER}s."
    echo "This is almost always a startup fault rather than a transient one — check"
    echo "the traceback above, then run '.venv/bin/python -m worker.main' directly."
    exit 1
  fi

  echo "worker exited ($code) after ${ran}s — restarting in ${backoff}s (failure $failures/$MAX_RAPID_FAILURES)"
  sleep "$backoff"
  backoff=$((backoff * 2))
  [ "$backoff" -gt "$BACKOFF_MAX" ] && backoff=$BACKOFF_MAX
done
