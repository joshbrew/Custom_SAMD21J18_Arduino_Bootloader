#!/bin/bash
# Linux and Raspberry Pi launcher. Keep this file beside tools/ and sketches/.
root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
if [ "$(uname -s)" != Linux ]; then
    printf '%s\n' 'Use the macOS .command or Windows .cmd launcher on this computer.' >&2
    exit 1
fi
if [ "$#" = 0 ] && { [ ! -t 0 ] || [ ! -t 1 ]; }; then
    if command -v x-terminal-emulator >/dev/null 2>&1; then
        exec x-terminal-emulator -e /bin/bash "$root/tools/samd21_launch.sh" linux
    elif command -v gnome-terminal >/dev/null 2>&1; then
        exec gnome-terminal -- /bin/bash "$root/tools/samd21_launch.sh" linux
    elif command -v konsole >/dev/null 2>&1; then
        exec konsole -e /bin/bash "$root/tools/samd21_launch.sh" linux
    elif command -v xterm >/dev/null 2>&1; then
        exec xterm -e /bin/bash "$root/tools/samd21_launch.sh" linux
    fi
    printf '%s\n' 'Open a terminal in this folder and run: bash SAMD21_Custom_Bringup_Linux.sh' >&2
    exit 1
fi
exec /bin/bash "$root/tools/samd21_launch.sh" linux "$@"
