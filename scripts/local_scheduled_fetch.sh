#!/bin/zsh

set -u

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR" || exit 1

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market-monitor local run started"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: repository has uncommitted changes; refusing to overwrite or commit them"
  exit 2
fi

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: $PYTHON is missing; create the virtual environment first"
  exit 2
fi

if ! git pull --ff-only origin main; then
  echo "ERROR: could not fast-forward from origin/main"
  exit 2
fi

ahead_count="$(git rev-list --count '@{upstream}..HEAD')"
if (( ahead_count > 0 )); then
  echo "Retrying push of $ahead_count previously committed local update(s)"
  if ! git push origin main; then
    echo "ERROR: a previous local data commit is still unable to reach GitHub"
    exit 3
  fi
fi

if "$PYTHON" -c '
import json
from pathlib import Path
from market_fetch.trading_calendar import resolve_default_target

root = Path.cwd()
target = resolve_default_target().isoformat()
latest = json.loads((root / "public/latest.json").read_text())
turnover = json.loads((root / "public/a_share_turnover.json").read_text())
current = (
    latest.get("status") == "ok"
    and latest.get("market_date") == target
    and latest.get("history_status") == "complete"
    and turnover.get("status") == "ok"
    and turnover.get("market_date") == target
    and turnover.get("coverage", {}).get("recent_20_complete") is True
)
raise SystemExit(0 if current else 1)
'; then
  echo "Data for the expected market date is already complete; nothing to do"
  exit 0
fi

"$PYTHON" -m market_fetch fetch
fetch_status=$?

"$PYTHON" -m market_fetch turnover
turnover_status=$?

if (( fetch_status != 0 )); then
  echo "WARNING: 159993 fetch failed; preserving the last published latest/history files"
  git restore --source=HEAD -- public/latest.json public/history/159993.json
fi

git add -- public
if git diff --cached --quiet; then
  echo "No publishable JSON changes"
else
  git commit -m "chore(data): local scheduled market update"
  if ! git push origin main; then
    echo "ERROR: generated data was committed locally but could not be pushed"
    exit 3
  fi
  echo "Data pushed; the deploy-only GitHub Action will publish public/ to Pages"
fi

if (( fetch_status != 0 || turnover_status != 0 )); then
  echo "ERROR: one or more collectors reported a failure; inspect this log"
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] market-monitor local run completed"
