#!/usr/bin/env bash
set -e
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$tool_dir"/*.sh "$tool_dir"/*.desktop "$tool_dir/j18_rpi.py"
if command -v gio >/dev/null 2>&1; then
    for launcher in "$tool_dir"/*.desktop; do
        gio set "$launcher" metadata::trusted true 2>/dev/null || true
    done
fi
printf '%s\n' 'Launchers are ready. Double-click a .desktop file in this folder.'
printf '%s\n' 'If the desktop asks, choose Trust/Allow Launching or Execute.'
