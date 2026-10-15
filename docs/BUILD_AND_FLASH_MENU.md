# Build and flash menu

Keep this whole repository folder together. Open the launcher for your computer:

| System | Top-level launcher |
| --- | --- |
| Windows | `SAMD21_Custom_Bringup.cmd` |
| Linux / Raspberry Pi | `SAMD21_Custom_Bringup_Linux.sh` |
| macOS | `SAMD21_Custom_Bringup_Mac.command` |

On Linux, run `bash SAMD21_Custom_Bringup_Linux.sh` in a terminal. A desktop launch
opens a terminal when a supported terminal application is installed. On macOS,
double-click the `.command` file. ZIP releases preserve executable permissions;
if copying from Windows removes them, run these once in the copied folder:

```sh
chmod +x SAMD21_Custom_Bringup_Linux.sh SAMD21_Custom_Bringup_Mac.command tools/samd21_launch.sh
```

## What each action means

**E** opens the [Arduino profile build/install/release workflow](ARDUINO_PROFILE_RELEASE.md).
Options 1/2 rebuild and install the default/configured profile; 3/4 export it;
5/6 rebuild, install and export. In BUILD/CHECK mode E previews without changes.
It never programs the connected board.

A fresh menu selects the repository Custom board/profile and bootloader, now
using the example crystal-less settings with the boot indicator disabled. Saved
selections override that initial choice; P/B still offer Zero, official loaders
and sketch-local custom builds.

**Options 1–4 flash the board when the header says FLASH.** Press **T** to switch
them to **BUILD / CHECK ONLY**, which builds missing files and checks the
OpenOCD configuration without opening the probe. The first run starts in FLASH mode;
later runs restore the last menu mode and selections for that operating system.

| Key | Action |
| --- | --- |
| 1 | Flash one combined `.with_bootloader.bin` at `0x0000`: selected board profile's bootloader, padding to 8 KB, selected sketch at `0x2000`. Zero uses the official Arduino loader; Custom uses the generated custom loader. |
| 2 | Flash two files: selected bootloader at `0x0000`, then selected sketch at `0x2000`. Both files are verified. |
| 3 | Flash only the selected bootloader, restoring 8 KB boot protection. Leaves application bytes intact. |
| 4 | Flash only the selected sketch at `0x2000`. Leaves bootloader bytes intact. |
| S | Choose a sketch from `sketches/<name>/<name>.ino`. |
| P | Choose the **compilation profile**: Arduino Zero, repository Custom defaults, or the configured custom profile for this sketch. |
| B | Choose the **bootloader binary**: current static file, installed core, pinned GitHub binary, repository Custom, configured custom loader, or a local file. |
| T | Toggle FLASH / BUILD-CHECK ONLY. |
| R | Recompile the selected sketch for the selected board profile. Does not flash. |
| L | Detailed custom bootloader configuration for the selected sketch: clock, LED, USB, aliases and boot states; compile a matching loader/profile. Does not flash. |
| G | Rebuild the repository default Custom bootloader and matching profile, using `bootloader/board.json` and `bootloader/src/`. Selects B option 4 and the repository Custom profile after success. Does not flash. |
| Q | Install/update a custom board profile in Arduino's sketchbook. Choose a catalog profile or another hardware-profile folder. T switches this to preview only. Does not flash. |
| I | Import a precompiled sketch-only `.bin`, recording the selected profile. The file must already have been compiled for that profile. |
| O | Open the selected sketch's static firmware folder. |
| D | Probe startup/inspect, recover under wired ST-LINK NRST (4), full probe with wired reset (5), or identify installed boot/sketch images among local compiled files (6). Halts the CPU; never programs flash. BUILD/CHECK only prepares files. |
| A | Linux: choose automatic adapter, USB ST-LINK, or Raspberry Pi 4 GPIO SWD. |
| U | Linux: toggle sudo for ST-LINK flashing. GPIO flashing always elevates OpenOCD. |
| 0 | Exit. |

The Zero option is **`arduino:samd:arduino_zero_native`** (Cortex M0+ native USB),
the stock Arduino target. The Custom option is
**`custom:samd:custom_j18`**, with the J18 pin aliases and linker layout.
The repository or sketch-local configured profile is used without changing your global Arduino
board installation. Both use an 8 KB bootloader offset. Board profile and
bootloader are independent choices for actions 2/3: Zero + Custom loader is
supported, for example. Action 1 follows P's board profile and ignores B.
Changing the profile automatically recompiles a conflicting saved
sketch on its next sketch action, using the same static folder. Compilation
must succeed before flashing continues; a failed compile preserves the previous
app and stops the action. Ordinary source changes still require **R**. Board
profile selection does not change the bootloader selected by B for action 3, or the SWD probe.

## Saved settings and detailed bootloader configuration

The menu remembers sketch, compilation profile, bootloader choice, local binary
path and FLASH/BUILD mode in `.build/menu/settings-<platform>.json`.
Unchanged settings are not rewritten. Saving retries transient file locks; if
Windows still refuses replacement, a warning leaves the menu running with the
current choices and preserves the last saved file. Windows uses a unique
temporary file and an atomic rename rather than metadata-merging `File.Replace`.
Linux also remembers adapter and sudo selection. Explicit command-line arguments
override saved choices; one-shot `--mode` / `-Mode` actions do not load or save menu
preferences. Missing sketches fall back to an available sketch; a missing local
loader falls back to the current static choice with a message. Delete the settings
file to restore initial menu defaults. These preferences stay out of release ZIPs.

Press **L** to configure the selected sketch's custom bootloader. The source folder
defaults to this repository's `bootloader/`; **F** selects another compatible source
folder. Settings and outputs stay in one fixed folder:

```text
sketches/<sketch>/.j18-build/custom-bootloader/
  board.json                  Editable settings for this target
  selection.json              Remembered source folder
  firmware/                   Loader binary, ELF, symbols, manifest and changes
  arduino-profile/             Matching Arduino hardware profile
```

To rebuild **B option 4**, press **G** in the main menu. This uses the repository's
`bootloader/board.json` and source, writing the default image to
`bootloader/firmware/samd21_sam_ba.bin` and refreshing its matching profile.
It does not promote sketch-local L settings into the repository defaults.
After success, the main menu selects the repository Custom board profile and
bootloader. A changed default profile automatically rebuilds a conflicting saved
sketch on the next sketch action, even though its FQBN stays the same.
Use action 1 or 2 to stage that loader with its matching sketch when ready.

| Configuration key | Meaning |
| --- | --- |
| 1 | External 32.768 kHz crystal or internal **CRYSTALLESS**, applied to both loader and sketch profile. |
| 2 / 3 | Enable the active-high boot indicator and select its physical GPIO. |
| 4 / 5 | USB manufacturer/product, VID, boot PID and application PID. IDs accept decimal or `0xHEX`. |
| 6 | Board revision and bootloader version strings. |
| 7 | Edit a GPIO alias or atomically swap two aliases. Reserved/duplicate pins and unreviewed peripheral routes are rejected. |
| 8 | Output LOW/HIGH or input with no pulls at boot. Interface pins default to input without pulls; outputs are explicit opt-ins. |
| 9 | Full review: all aliases, purposes, boot states, SPI validity, USB details, fixed memory layout, last build size/checksum and settings status. |
| H | Detailed field-by-field help and limitations. |
| V | Validate settings and supported routes without compiling. |
| J | Import a compatible `board.json` into this target. |
| F | Choose another compatible source folder, loading its defaults into this target. |
| C | Compile loader and matching board profile; build twice and require identical bytes. |
| 0 | Return, retaining accepted edits. |

After **C** succeeds and you return, the main menu selects **Configured custom**
for both P and B. Action **1** uses its loader in the combined image. Action **2**
uses the separately selected loader. The next sketch action automatically rebuilds
if its saved custom configuration differs, even when the FQBN is unchanged.
Settings edited after compilation require another **C**; stale builds are rejected.
Q also offers installation of this sketch's configured profile into Arduino IDE.

The fixed target remains **SAMD21J18A**, with an 8 KB boot region, application at
`0x2000`, native USB SAM-BA and the existing double-tap cell. A different MCU or
unreviewed SERCOM routing requires driver/linker changes. Another source folder
must implement the compatible `build.py --board-config --out --profile-out`
interface and the validated board schema. GPIO changes also require corresponding
sketch `config.h`/driver changes and physical rewiring; the menu does not infer them.

For the reported XOSC32K startup wait, **L → 1 → 2 (internal) → C → 0** prepares
a crystal-less loader/profile. Then action **1 or 2** builds the matching sketch;
action **3 alone cannot change the sketch's clock initialization**. This is a
diagnostic alternative to repairing the fitted crystal circuit. USB recovery needs
USB SOF for accurate clock locking; verify UART and other timing-sensitive features
without USB on the bench. Native USB still requires correct physical USB wiring.

Fresh compilation also supplies the static bootloader's byte count and CRC to
the feature-verification sketch, when that loader file is available. If you
swap loaders while reusing an app, its compiled checksum expectation remains
unchanged. To update that check, select the loader and run action 2 in
BUILD/CHECK mode to stage it, then **R** to recompile. Sketch-only compilation
without a static loader uses the selected profile's built-in identity.

## One static folder per sketch

Files live in `sketches/<selected sketch>/.j18-build/firmware/`:

```text
samd21_sam_ba.bin                 Selected bootloader; may be replaced manually
firmware.sketch.bin               Saved application linked at 0x2000
firmware.sketch.elf               Symbols retained from successful compilation
firmware.with_bootloader.bin      Combined image regenerated by action 1
flash_separate.cfg               Generated OpenOCD configuration
```

Normal runs reuse the application, even after source changes. A missing app or
conflicting board profile compiles automatically; **R** / `--rebuild` explicitly replaces it. Deleting the build
folder also starts fresh. The folder name stays the same. Failed compilation
keeps the previously published application. **Current static file** preserves
manual bootloader replacements; selecting another loader explicitly replaces
that file. Action 1 stages the selected board profile's loader into the static
folder (official Arduino for Zero, generated custom loader for Custom).
The GitHub option uses the verified bundled ArduinoCore-samd **1.8.14** binary,
not a changing `master` download. The installed-core option uses the same pin.

## Tools and wiring

The launchers find or install Arduino CLI and OpenOCD when required, and install
Arduino SAMD core 1.8.14 before compiling. Linux automatic OpenOCD installation
uses apt; other distributions can install OpenOCD themselves and add it to PATH.
macOS uses Homebrew. Set `J18_ARDUINO_CLI`, `J18_OPENOCD`, or (Unix launchers)
`J18_PYTHON` to use explicit executables. A saved application does not require
Arduino CLI to flash in sketch-only mode.

ST-LINK: SWDIO → PA31, SWCLK → PA30, common GND; power the target normally.
No reset wire is used. Windows checks the ST-LINK driver and can install a
matching signed driver when missing; see the Windows tools README.
Linux ST-LINK needs device access through udev rules or **U** to elevate only
OpenOCD. Pi 4 GPIO uses BCM8 (physical 24) → PA31 and BCM11 (physical 23) → PA30.
GPIO selection checks the Pi model and OpenOCD driver; other Pi models,
including Pi 5, should use ST-LINK. Linux builds run as the normal user.

## Verification

Before opening the probe, image vectors and sizes are checked. Combined images
are constructed with the application exactly at `0x2000`. During programming,
OpenOCD reads back the flash and compares it to the selected binary; it repeats
verification after reset/boot-protection updates and starts the firmware only
after successful verification. This confirms the stored bytes. LED, USB and
peripheral behavior require separate tests on the actual board; the included
Blink example does not validate external devices.

## Noninteractive examples (no hardware access)

```powershell
.\SAMD21_Custom_Bringup.cmd -List
.\SAMD21_Custom_Bringup.cmd -Mode app -Sketch Blink -Board custom -BuildOnly -Rebuild
.\SAMD21_Custom_Bringup.cmd -Mode separate -Sketch Blink -Board zero -Bootloader github -BuildOnly
```

```sh
bash SAMD21_Custom_Bringup_Linux.sh --list
bash SAMD21_Custom_Bringup_Linux.sh --mode app --sketch Blink --board custom --build-only --rebuild --adapter stlink
./SAMD21_Custom_Bringup_Mac.command --mode separate --sketch Blink --board zero --bootloader github --build-only
```

Remove `-BuildOnly` / `--build-only` only when you intend to program hardware.
