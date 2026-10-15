#!/bin/bash
set -e
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$tool_dir"/*.command "$tool_dir"/*.sh "$tool_dir/j18_mac.py"
printf '%s\n' 'Launchers are ready. Double-click a .command file in this folder.'
