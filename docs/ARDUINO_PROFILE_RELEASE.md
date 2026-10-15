# Build, install and release an Arduino board profile

Run commands from the `SAMD21_Custom_Bringup` repository root using Python 3.10+.
On Windows, `py -3` can replace `python`; Linux/macOS commonly use `python3`.

```sh
python tools/arduino_profile_workflow.py --build --install --export
```

This rebuilds and repeat-verifies the repository default bootloader, generates
its matching Arduino profile, installs it into Arduino's configured sketchbook,
checks board discovery, and exports a portable folder and ZIP. No device is
flashed. Restart Arduino IDE to refresh the board definition.

The same workflow is available in **E → 5** on Windows, Linux/Raspberry Pi and
macOS. E offers separate build/install and export actions for the repository
default or the selected sketch's custom configuration. In BUILD/CHECK mode E
previews the requested workflow without building, installing or exporting.
Q remains the installer for existing profiles, including other profile folders.

For an already configured sketch-local loader/profile (set up with L):

```sh
python tools/arduino_profile_workflow.py --target sketch --sketch Blink --build --install --export
```

Choose any combination of `--build`, `--install`, `--export`. `--check-only`
previews all requested actions. `--sketchbook PATH` overrides the install target;
`--arduino-cli PATH` and `--arduino-package PATH` support nonstandard tool locations.
`--output PATH` sets a managed export folder; an unrelated existing folder is
rejected. Settings/profile mismatches stop installation and export until rebuilt.
A failed build stops the remaining actions.

## Release contents and installation

The default export is `dist/Custom_Arduino_Profile/` and
`dist/Custom_Arduino_Profile.zip`. Sketch-local exports append the sketch name.

```text
Custom_Arduino_Profile/
  hardware/custom/samd/
    boards.txt
    variants/custom_j18/
    bootloaders/custom/
    LICENSE
  tools/                     # Installer and shared helper implementation
  install_profile.py
  arduino-profiles.json
  PROFILE_MANIFEST.json
  README.md
```

The tree includes the generated variant, linker script, bootloader binary,
identity/manifests, and LGPL notice. The manifest hashes every payload file.
The Arduino SAMD core/toolchain are prerequisites rather than copied into this
release. An extracted release is independent of the bring-up repository.

From the extracted folder:

```sh
python install_profile.py
```

Arduino CLI must be installed/in PATH, supplied with `--arduino-cli`, or available
inside Arduino IDE on Windows. The installer uses the CLI sketchbook setting,
ensures `arduino:samd@1.8.14`, retains a prior-profile backup, and rolls back if
Arduino board discovery fails. `--check-only` previews without installation.
No SWD adapter is needed for profile installation.

For manual installation, copy `hardware/custom/samd` into the sketchbook's
`hardware/custom/samd`, install `arduino:samd@1.8.14`, restart Arduino IDE and select
**Custom SAMD21J18 (Native USB)** (`custom:samd:custom_j18`). This follows
Arduino's [local hardware platform layout](https://docs.arduino.cc/arduino-cli/platform-specification/).

## Shipping with another project

The folder/ZIP and installer can ship alongside another project's release assets or be called
from that project's setup workflow. Installing an Arduino library alone does not register
a board profile. This export keeps the existing `custom:samd:custom_j18` identity
and its dependency on Arduino's SAMD core; it does not merge into or modify an
existing installed platform.

For installation through Boards Manager instead, prepare and host a platform
archive and a [package index](https://docs.arduino.cc/arduino-cli/package_index_json-specification/)
with the appropriate core/tool dependencies. This workflow prepares a local-profile
release; it does not create or publish that index.
