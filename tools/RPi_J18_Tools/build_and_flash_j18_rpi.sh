#!/usr/bin/env bash
set -e
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$tool_dir/j18_rpi.py" --mode combined "$@"
