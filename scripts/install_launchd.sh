#!/bin/zsh

set -eu

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.liberalalice.market-monitor"
SOURCE_PLIST="$PROJECT_DIR/launchd/$LABEL.plist"
DESTINATION_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
launchctl bootout "$DOMAIN" "$DESTINATION_PLIST" 2>/dev/null || true
cp "$SOURCE_PLIST" "$DESTINATION_PLIST"
plutil -lint "$DESTINATION_PLIST"
launchctl bootstrap "$DOMAIN" "$DESTINATION_PLIST"
launchctl enable "$DOMAIN/$LABEL"
launchctl print "$DOMAIN/$LABEL"
