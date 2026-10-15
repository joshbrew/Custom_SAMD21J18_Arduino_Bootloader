#!/bin/bash
set -u
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
platform="$1"
shift
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ "$(id -u)" = 0 ]; then
    printf '%s\n' 'Run this menu as your normal user. GPIO flashing elevates only OpenOCD.' >&2
    exit 1
fi
find_python() {
    for candidate in "${J18_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        if [ "$platform" = mac ] && [ "$candidate" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then continue; fi
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}
python="$(find_python || true)"
if [ -z "$python" ]; then
    if [ "$platform" = mac ]; then
        /bin/bash "$tool_dir/Mac_J18_Tools/ensure_homebrew.sh" || exit $?
        brew="$(command -v brew || true)"
        [ -n "$brew" ] || { printf '%s\n' 'Homebrew was not found.' >&2; exit 1; }
        "$brew" install python || exit $?
    elif command -v apt-get >/dev/null 2>&1; then
        printf '%s\n' 'Installing Python with the system package manager...'
        sudo apt-get update || exit $?
        sudo apt-get install -y python3 ca-certificates || exit $?
    else
        printf '%s\n' 'Install Python 3.10+ or set J18_PYTHON to its executable, then rerun.' >&2
        exit 1
    fi
    python="$(find_python || true)"
    [ -n "$python" ] || { printf '%s\n' 'Python 3.10+ is required; this OS may need a newer Python package.' >&2; exit 1; }
fi
exec "$python" "$tool_dir/samd21_menu.py" --platform "$platform" "$@"
