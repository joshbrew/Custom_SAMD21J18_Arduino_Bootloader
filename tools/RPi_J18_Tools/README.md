# Raspberry Pi SAMD21J18 build and flash tools

Copy this **whole folder** into a sketch folder on the Raspberry Pi. Keep all files together. The tools look for `.ino` files in **their own folder first**, then the **parent** folder. If both contain sketches, their own folder wins. An ambiguous sketch produces an error rather than silently selecting a different folder. The usual layout is:

```text
samd_blinky_test/
  samd_blinky_test.ino
  samd21_sam_ba.bin                 optional local override
  RPi_J18_Tools/
    README.md
    j18_rpi.py
    setup_launchers.sh
    build_and_flash_j18_rpi.sh
    build_and_flash_j18_separate_rpi.sh
    samd21_sam_ba.bin               bundled official bootloader
    ...desktop launchers...
```

## One-time setup

Raspberry Pi OS with a desktop and Python 3 is expected. Regular Linux computers can use USB ST-LINK; automatic OpenOCD installation requires `apt-get` (Debian/Ubuntu). On other distributions, install OpenOCD 0.12+ yourself. Arduino CLI downloads cover x86-64, ARM64, and supported 32-bit ARM. Open a terminal in the sketch folder and run:

```bash
bash RPi_J18_Tools/setup_launchers.sh
```

Then double-click a `.desktop` launcher **inside RPi_J18_Tools**. If the desktop asks, choose **Trust/Allow Launching** or **Execute**. Launchers open a terminal that stays open until you press Enter. If the file manager opens a launcher as text, enable its executable permission or run the shell command below instead.

The launchers resolve their own location, so the folder can be copied into another sketch without editing absolute paths. Rerun setup after copying if executable/trust metadata was not preserved.

## Choose a launcher

| Launcher | Workflow and adapter |
|---|---|
| `Flash combined.desktop` | Combined bootloader and sketch; automatically chooses Pi 4 GPIO when supported, otherwise USB ST-LINK. |
| `Flash separate bootloader.desktop` | Standalone bootloader followed by sketch; same automatic adapter selection. |
| `Flash combined USB ST-LINK.desktop` | Combined image, explicitly using a USB ST-LINK. |
| `Flash separate USB ST-LINK.desktop` | Standalone bootloader then sketch, explicitly using a USB ST-LINK. |

Automatic GPIO selection requires a **Pi 4** and OpenOCD built with `bcm2835gpio`. Use a USB ST-LINK launcher if GPIO is unavailable or you wired an ST-LINK instead. A Pi 5 uses USB ST-LINK with this package; its different GPIO controller needs a different GPIO configuration.

## Wiring

### Pi 4 GPIO adapter

| Raspberry Pi 4 | SAMD21 target |
|---|---|
| BCM8 / physical pin 24 | PA31 / SWDIO |
| BCM11 / physical pin 23 | PA30 / SWCLK |
| GND | GND |

No ST-LINK is used in GPIO mode. The reset wire is not used. Power the target normally with 3.3 V logic levels; RESET must idle high.

### USB ST-LINK adapter

Plug the ST-LINK into the Pi's USB port. Connect ST-LINK SWDIO to PA31, SWCLK to PA30, GND to target GND, and VTref (if available) to target 3.3 V. Check the adapter pinout; a power-output pin is not necessarily VTref. No reset wire is required.

## What the tools do

- Check Arduino CLI and OpenOCD; install missing tools when needed.
- Download a SHA256-checked Arduino CLI for the Pi's 32-bit or 64-bit Linux architecture. OpenOCD is installed through `apt-get` when missing.
- Install the `arduino:samd` core when needed and compile for **Arduino Zero (Native USB)**.
- Show **Compiling sketch...**, **Building firmware...**, and live command output.
- Build into a new `.j18-build` subfolder in the sketch folder, validate vector tables and sizes, then check the OpenOCD configuration before flashing.
- Flash and verify the image, restore 8 KB bootloader protection, and start the sketch.

Compilation runs as your normal user. The terminal may ask for your `sudo` password for package installation or flashing. Do not run the whole launcher with sudo; let it elevate only the steps that need it.

The combined workflow uses the official bootloader in the selected installed Arduino core and flashes one image at `0x00000000`. The separate workflow flashes `samd21_sam_ba.bin` at `0x00000000`, then the sketch-only binary at `0x00002000` in another OpenOCD run.

## Standalone bootloader selection

The separate workflow checks:

1. `samd21_sam_ba.bin` beside the sketch `.ino`.
2. The sketch's `build` folder and its subfolders.
3. The sketch's `.j18-build` folder and its subfolders.
4. The copy included in `RPi_J18_Tools`.
5. If no copy exists, download from the [ArduinoCore-samd repository](https://github.com/arduino/ArduinoCore-samd/blob/master/bootloaders/zero/samd21_sam_ba.bin).

The included official binary is 6,504 bytes, with SHA256:

```text
89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b
```

The path and hash used are printed. Different bootloader copies within one build folder require an explicit copy beside the sketch.

## Terminal commands

From the sketch folder:

```bash
# Check tools and adapter selection without installing or contacting the target.
bash RPi_J18_Tools/build_and_flash_j18_rpi.sh --check

# Compile and validate configuration without flashing.
bash RPi_J18_Tools/build_and_flash_j18_separate_rpi.sh --build-only

# Compile and flash using Pi 4 GPIO.
bash RPi_J18_Tools/build_and_flash_j18_rpi.sh --adapter gpio

# Compile, flash the standalone bootloader, then sketch through USB ST-LINK.
bash RPi_J18_Tools/build_and_flash_j18_separate_rpi.sh --adapter stlink
```

`--build-only` can install missing tools and board support. `--check` does not. Optional `J18_ARDUINO_CLI` and `J18_OPENOCD` environment variables select existing nonstandard tool paths.

## Troubleshooting

- **No .ino found:** place the `.ino` inside this tools folder or in its parent folder. An explicit `--sketch-dir /path/to/sketch` selects a different location.
- **Multiple .ino files:** use a main `.ino` matching the parent folder's name. A single differently named `.ino` is staged automatically.
- **Missing libraries:** install the project's libraries with Arduino CLI, or provide a `libraries` folder beside the `.ino`.
- **OpenOCD older than 0.12:** update the system package or point `J18_OPENOCD` to a newer installation.
- **USB open failed:** check the ST-LINK USB connection and close other software holding it.
- **GPIO cannot connect:** check BCM numbering, common GND, power and target SWD pins. These GPIO settings are for Pi 4.
- **Verified but no blink:** fully remove board power, including battery and module back-power, then reconnect. Verify the LED pin in your actual sketch.

Logs, firmware and generated OpenOCD configuration are kept under `.j18-build` in the sketch folder. The source sketch is not edited.

The build/configuration logic has been checked without flashing hardware. GPIO timing, Linux package setup, and desktop behavior must be confirmed on your Raspberry Pi.

## Sketch-specific core overrides

If the selected sketch contains `J18BuildOverrides.h`, the launcher applies it to both sketch and Arduino core C++ compilation. Custom uses this to disable the Zero USB activity LEDs on conflicting peripheral pins. Copy the header with that sketch; a plain Arduino IDE build does not apply the override.
