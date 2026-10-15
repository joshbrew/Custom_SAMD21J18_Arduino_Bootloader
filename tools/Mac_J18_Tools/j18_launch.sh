#!/bin/bash
set -u
tool_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
pause=0
check=0
engine_args=()
for arg in "$@"; do
    [ "$arg" != --pause ] || pause=1
    [ "$arg" != --check ] || check=1
    if [ "$arg" != --pause ]; then engine_args+=("$arg"); fi
done
finish() {
    status=$?
    if [ "$pause" = 1 ] && [ -t 0 ]; then
        printf '\n%s' 'Press Enter to close...'
        read -r answer || true
    fi
    exit "$status"
}
trap finish EXIT
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'These launchers require macOS.' >&2
    exit 1
fi
if [ "$(id -u)" = 0 ]; then
    printf '%s\n' 'Run as your normal user, without sudo.' >&2
    exit 1
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
find_python() {
    for python in "${J18_PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
        [ -n "$python" ] && [ -x "$python" ] || continue
        # Avoid invoking Apple's developer-tools shim when those tools are absent.
        if [ "$python" = /usr/bin/python3 ] && ! /usr/bin/xcode-select -p >/dev/null 2>&1; then continue; fi
        if "$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$python"
            return 0
        fi
    done
    return 1
}
python="$(find_python || true)"
if [ -z "$python" ]; then
    if [ "$check" = 1 ]; then
        printf '%s\n' 'Python 3.8+ is missing. A normal run will install it using Homebrew.'
        for tool in brew arduino-cli openocd; do
            printf '%s: %s\n' "$tool" "$(command -v "$tool" || printf '%s' missing)"
        done
        printf '%s\n' 'No installation, compilation, or target access performed.'
        exit 1
    fi
    /bin/bash "$tool_dir/ensure_homebrew.sh" || exit $?
    brew="$(command -v brew || true)"
    [ -n "$brew" ] || { printf '%s\n' 'Homebrew was not found after setup.' >&2; exit 1; }
    printf '%s\n' 'Installing Python 3 with Homebrew...'
    "$brew" install python || exit $?
    python="$(find_python || true)"
    [ -n "$python" ] || { printf '%s\n' 'Python 3.8+ was not found after setup.' >&2; exit 1; }
fi
"$python" "$tool_dir/j18_mac.py" "${engine_args[@]}"
exit $?
