#!/bin/zsh

set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.liberalalice.market-monitor"
SOURCE_PLIST="$PROJECT_DIR/launchd/$LABEL.plist"
DESTINATION_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ASCII_PROJECT_LINK="$HOME/market-monitor-local"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
if [[ -L "$ASCII_PROJECT_LINK" ]]; then
  if [[ "$(readlink "$ASCII_PROJECT_LINK")" != "$PROJECT_DIR" ]]; then
    echo "ERROR: $ASCII_PROJECT_LINK points to a different project" >&2
    exit 2
  fi
elif [[ -e "$ASCII_PROJECT_LINK" ]]; then
  echo "ERROR: $ASCII_PROJECT_LINK exists and is not a symbolic link" >&2
  exit 2
else
  ln -s "$PROJECT_DIR" "$ASCII_PROJECT_LINK"
fi
launchctl bootout "$DOMAIN" "$DESTINATION_PLIST" 2>/dev/null || true
cp "$SOURCE_PLIST" "$DESTINATION_PLIST"
plutil -lint "$DESTINATION_PLIST"
launchctl bootstrap "$DOMAIN" "$DESTINATION_PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl print "$DOMAIN/$LABEL"
