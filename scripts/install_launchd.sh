#!/bin/zsh

set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.liberalalice.market-monitor"
SOURCE_PLIST="$PROJECT_DIR/launchd/$LABEL.plist"
DESTINATION_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
RUNTIME_DIR="$HOME/market-monitor-runtime"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
if [[ -d "$RUNTIME_DIR/.git" ]]; then
  if [[ -n "$(git -C "$RUNTIME_DIR" status --porcelain)" ]]; then
    echo "ERROR: $RUNTIME_DIR has uncommitted changes" >&2
    exit 2
  fi
  git -C "$RUNTIME_DIR" pull --ff-only origin main
elif [[ -e "$RUNTIME_DIR" ]]; then
  echo "ERROR: $RUNTIME_DIR exists and is not a market-monitor Git checkout" >&2
  exit 2
else
  git clone git@github.com:LiberalAlice/market-monitor.git "$RUNTIME_DIR"
fi
if [[ ! -x "$RUNTIME_DIR/.venv/bin/python" ]]; then
  "$PROJECT_DIR/.venv/bin/python" -m venv "$RUNTIME_DIR/.venv"
  "$RUNTIME_DIR/.venv/bin/python" -m pip install -r "$RUNTIME_DIR/requirements.txt"
fi
launchctl bootout "$DOMAIN" "$DESTINATION_PLIST" 2>/dev/null || true
cp "$SOURCE_PLIST" "$DESTINATION_PLIST"
plutil -lint "$DESTINATION_PLIST"
launchctl bootstrap "$DOMAIN" "$DESTINATION_PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl print "$DOMAIN/$LABEL"
