# Install custom Arduino board profiles

Open the top-level menu on Windows, Linux/Pi or macOS and press **Q**.
Choose the Custom profile, or another folder containing
`hardware/<vendor>/<architecture>/boards.txt`. With **T → BUILD/CHECK ONLY**, Q
previews the installation without changing installed board profiles.

This copies the board definitions, variant files and included bootloader images
into the Arduino sketchbook's `hardware/<vendor>/<architecture>` directory.
It installs declared core dependencies when missing, verifies the copied files,
and checks that Arduino CLI discovers the board identifiers. It does not flash
any device. Restart Arduino IDE afterward and select
**Custom SAMD21J18 (Native USB)** in its board selector.

The sketchbook location comes from `arduino-cli config get directories.user`.
If Arduino IDE uses a different Sketchbook location, pass `--sketchbook` or the
menu's `-ProfileSketchbook` / `--profile-sketchbook` option with that location.
The menu can compile using the repository's local profile without this global
installation; Q makes it available to your normal Arduino IDE/CLI setup.

## Commands

Windows:

```powershell
.\SAMD21_Custom_Bringup.cmd -Mode install-profile -BuildOnly
.\SAMD21_Custom_Bringup.cmd -Mode install-profile
```

Linux / Raspberry Pi:

```sh
bash SAMD21_Custom_Bringup_Linux.sh --mode install-profile --build-only
bash SAMD21_Custom_Bringup_Linux.sh --mode install-profile
```

macOS:

```sh
./SAMD21_Custom_Bringup_Mac.command --mode install-profile --build-only
./SAMD21_Custom_Bringup_Mac.command --mode install-profile
```

The standalone tool requires Python 3.10+ and Arduino CLI. On Windows use
`py -3` in place of `python3`:

```sh
python3 tools/install_board_profile.py --list
python3 tools/install_board_profile.py --profile custom
python3 tools/install_board_profile.py --source /path/to/new-profile-root --require-core arduino:samd@1.8.14
```

A direct platform folder is also supported:

```sh
python3 tools/install_board_profile.py --source /path/to/platform --vendor myboards --architecture samd --require-core arduino:samd@1.8.14
```

`--arduino-cli` or `J18_ARDUINO_CLI` selects an explicit CLI executable.
`--sketchbook` selects a different installation destination. Core dependencies
must use `vendor:architecture@version`; they must be available through the CLI's
configured package indexes. Use `--check-only` for a preview.

## Future revisions and other profiles

The default profile catalog is `arduino-profiles.json`. Add another entry with
a unique ID, display name, source root, and pinned `required_cores`. Q lists
catalog entries automatically. `--catalog` can select another catalog file.
The installer discovers platform vendor/architecture folders and board IDs
from `boards.txt`; it does not hard-code Custom pin names, MCU type or FQBN.

The Custom catalog entry checks the generated files against the bootloader
release's `profile_sha256` manifest and verifies its embedded manifest copy.
After changing `bootloader/board.json`, rebuild with **L** (or
`python3 bootloader/build.py --verify-repeat`), then **Q** to install the updated
generated profile. Do not hand-edit generated files while retaining an old
integrity manifest. Other catalog entries can supply their own integrity
manifest/field/platform, or omit integrity for an experimental source folder.

The flashing menu's P selector offers Zero, repository Custom defaults, and the
sketch-local custom Custom profile built with L. Q can install that configured
profile too. Installing a different profile makes it available in Arduino;
it does not add arbitrary MCU targets to the SAMD21 SWD flashing routine.

## Updates and recovery

Only the selected vendor/architecture platform is updated. Unrelated installed
platforms and extra user files are retained. Files previously managed by the
installer and removed from the new source are removed only if they have not
been edited locally. The last installed platform is backed up at:

```text
<sketchbook>/.arduino-profile-installer/<vendor>/<architecture>/previous/
```

The backup location is static and retains one previous version. An unchanged
reinstallation keeps that backup. Updates are staged before replacing the
platform. If Arduino CLI cannot discover the expected board afterward, the
installer restores the previous platform. Symlinks/reparse points and paths
that escape the platform/state folders are rejected. Close Arduino IDE while
updating profiles, then reopen it to refresh the list.

This follows Arduino's documented
[manual hardware-platform layout and core references](https://docs.arduino.cc/arduino-cli/platform-specification/).
It does not configure IDE **Burn Bootloader** for Custom; use the explicit SWD
flash actions to program the included bootloader binary.
