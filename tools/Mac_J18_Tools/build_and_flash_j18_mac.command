#!/bin/bash
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec /bin/bash "$tool_dir/j18_launch.sh" --mode combined --pause "$@"
