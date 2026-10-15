#!/bin/bash
# Finder opens .command files in Terminal.
root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'This launcher requires macOS.' >&2
    exit 1
fi
/bin/bash "$root/tools/samd21_launch.sh" mac "$@"
status=$?
if [ "$#" = 0 ] && [ -t 0 ]; then
    printf '\n%s' 'Press Enter to close...'
    read -r answer || true
fi
exit "$status"
