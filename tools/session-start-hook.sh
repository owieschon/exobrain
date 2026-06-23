#!/bin/bash
# Session-start hook for Claude Code.
#
# Surfaces two pending files into the session context:
#   1. pending-reconciliation.txt (from distill.py)    -- unfinished-session alerts
#   2. tools/pending-ingest.txt   (from auto_ingest.py) -- drafts needing review
#
# Fast by design: file reads only, no pipeline run and no API calls. The final
# JSON is emitted with `python3 json.dumps` so quoting and newline escaping are
# always correct (rather than hand-rolled with sed).
#
# Wire into Claude Code's SessionStart hook in settings.json:
#   { "hooks": { "SessionStart": [ { "type": "command",
#       "command": "bash /path/to/exobrain/tools/session-start-hook.sh" } ] } }

# Honor the BRAIN_DIR env override (matching the Python tools); otherwise derive
# it from this script's location (the parent of tools/).
if [ -z "$BRAIN_DIR" ]; then
    BRAIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
PENDING_RECONCILIATION="$BRAIN_DIR/pending-reconciliation.txt"
PENDING_INGEST="$BRAIN_DIR/tools/pending-ingest.txt"

context=""

# --- Reconciliation alerts (from distill.py) ---
if [ -f "$PENDING_RECONCILIATION" ] && [ -s "$PENDING_RECONCILIATION" ]; then
    context+="## Unfinished sessions detected"$'\n\n'
    context+="The following sessions ended with pending tool calls. You may want to resume or clean them up:"$'\n\n'
    context+="$(cat "$PENDING_RECONCILIATION")"$'\n\n'
    context+="Run \`python3 $BRAIN_DIR/tools/distill.py\` to process the backlog."
    rm -f "$PENDING_RECONCILIATION"
fi

# --- Ingest alerts (from auto_ingest.py) ---
if [ -f "$PENDING_INGEST" ] && [ -s "$PENDING_INGEST" ]; then
    [ -n "$context" ] && context+=$'\n\n'
    context+="## Pending ingest items"$'\n\n'
    context+="The following session captures need human review before they can be added to the wiki. RED items (contradictions) are listed first and need a decision (supersede or discard); YELLOW and GREEN items are staged proposals ready for approval."$'\n\n'
    context+="$(cat "$PENDING_INGEST")"$'\n\n'
    context+="Review staged files in \`$BRAIN_DIR/tools/staged/\` and approve or discard each item."
    rm -f "$PENDING_INGEST"
fi

# Emit JSON only when there is something to surface.
if [ -n "$context" ]; then
    CONTEXT="$context" python3 -c 'import json, os; print(json.dumps({"hookSpecificOutput": {"additionalContext": os.environ["CONTEXT"]}}))'
fi
