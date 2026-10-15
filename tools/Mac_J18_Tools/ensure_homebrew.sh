#!/bin/bash
set -e
if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'Homebrew setup in this package requires macOS.' >&2
    exit 1
fi
if [ "$(id -u)" = 0 ]; then
    printf '%s\n' 'Run this tool as your normal user, without sudo.' >&2
    exit 1
fi
for brew in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$brew" ]; then exit 0; fi
done
if command -v brew >/dev/null 2>&1; then exit 0; fi
printf '%s\n' 'Homebrew is missing. Starting its official installer from https://brew.sh.'
printf '%s\n' 'Follow its prompts; initial setup may request an administrator password and developer tools.'
installer="$(mktemp -t j18-homebrew)"
trap 'rm -f -- "$installer"' EXIT
/usr/bin/curl --fail --show-error --location --connect-timeout 20 --max-time 90 \
    https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer"
/bin/bash "$installer"
