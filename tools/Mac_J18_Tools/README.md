# macOS SAMD21J18 build and flash tools

Copy this **whole folder** into an Arduino sketch folder on the Mac. The launchers search for `.ino` files **in this tools folder first**, then **in its parent folder**. The tools folder wins if both locations contain sketches. Multiple `.ino` files require a main sketch matching the selected folder name; an ambiguous sketch stops with an error.

```text
samd_blinky_test/
  samd_blinky_test.ino
  Mac_J18_Tools/
    build_and_flash_j18_mac.command
    build_and_flash_j18_separate_mac.command
    j18_mac.py
    ...keep all package files together...
```

## Run on the Mac

1. Extract the ZIP and copy `Mac_J18_Tools` into the sketch folder.
2. If executable permissions were lost, open Terminal in the sketch folder and run `bash Mac_J18_Tools/setup_launchers.sh` once.
3. Double-click the desired `.command` launcher. Terminal opens and stays open until you press Enter.

| Launcher | What it flashes |
|---|---|
| `build_and_flash_j18_mac.command` | A combined image at `0x00000000`, using the official bootloader from the selected Arduino SAMD core. |
| `build_and_flash_j18_separate_mac.command` | Standalone `samd21_sam_ba.bin` at `0x00000000`, then a freshly compiled sketch-only image at `0x00002000`. |

Both workflows compile for **Arduino Zero (Native USB)** and flash through **USB ST-LINK**, without a reset wire. There is no Raspberry Pi GPIO mode on Mac.

## Tool setup

The launchers use existing Python 3.8+, Arduino CLI, and OpenOCD when available, including common Homebrew locations. Arduino IDE's bundled CLI is also detected in `/Applications` and `~/Applications`. Missing tools are installed through [Homebrew](https://brew.sh), using `python`, [`arduino-cli`](https://docs.arduino.cc/arduino-cli/installation), and [`open-ocd`](https://formulae.brew.sh/formula/open-ocd). If Homebrew is missing, its official installer starts and presents its own prompts. Initial setup may request your administrator password and Apple's developer tools. Run as your normal user, without `sudo`.

Apple Silicon and Intel tool locations are supported. Availability of Homebrew packages depends on your Mac and macOS version; see [Homebrew requirements](https://docs.brew.sh/Installation). Some Arduino SAMD compiler packages contain Intel executables: on Apple Silicon, an error such as **bad CPU type in executable** may require Rosetta. Follow Apple's installation prompt or the [Arduino troubleshooting instructions](https://support.arduino.cc/hc/en-us/articles/7765785712156-Error-bad-CPU-type-in-executable-on-macOS), then rerun.

OpenOCD must be version 0.12 or newer. Older detected versions trigger a Homebrew upgrade. SAMD board support is installed if missing. No Windows ST-LINK driver installer is used; OpenOCD uses its macOS USB libraries. Close other programs using the adapter. If macOS requests permission for a USB accessory, allow the connected ST-LINK.

## Wiring

| ST-LINK | SAMD21 target |
|---|---|
| SWDIO | PA31 |
| SWCLK | PA30 |
| GND | GND |
| VTref, if present | Target 3.3 V reference |

Power the target normally. Follow the adapter's pinout: a power-output pin is not necessarily VTref. RESET must idle high; no reset wire is required.

## Commands and checks

From the sketch folder:

```bash
# Check existing tools without installing, compiling, or contacting the target.
bash Mac_J18_Tools/build_and_flash_j18_mac.command --check

# Compile and check the configuration without flashing.
bash Mac_J18_Tools/build_and_flash_j18_mac.command --build-only

# Compile and flash the standalone bootloader, then the sketch.
bash Mac_J18_Tools/build_and_flash_j18_separate_mac.command

# Select a different sketch folder explicitly.
bash Mac_J18_Tools/build_and_flash_j18_mac.command --sketch-dir "/path/to/MySketch"
```

`--build-only` may install missing tools and board support. `--check` never installs. Optional `J18_PYTHON`, `J18_ARDUINO_CLI`, and `J18_OPENOCD` variables select nonstandard tool paths. The launchers print **Compiling sketch...**, **Building firmware...**, and live tool output.

The separate workflow searches for the bootloader beside the selected sketch, recursively in its `build` folder, recursively in `.j18-build`, then in this tools package. It downloads the official [ArduinoCore-samd binary](https://github.com/arduino/ArduinoCore-samd/blob/master/bootloaders/zero/samd21_sam_ba.bin) if none exists. Different bootloader copies within one build folder require an explicit copy beside the sketch. The bundled official binary's SHA256 is `89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b`.

Each run compiles afresh, validates vectors and sizes, and checks OpenOCD configurations before flashing. Successful flashing includes verification, restoring 8 KB bootloader protection, and starting the sketch. Firmware and logs stay under the selected sketch's `.j18-build` folder. A `libraries` folder beside the sketch is included when present; other required libraries must already be installed.

If Finder refuses to launch a downloaded file, use macOS's normal Open/Privacy & Security controls for files you trust, or run the shown Terminal command. The tools do not disable macOS security checks.

The firmware build and configuration logic has been checked on Windows without contacting a target. Homebrew setup, Finder launching, and hardware flashing need verification on a Mac.

## Sketch-specific core overrides

If the selected sketch contains `J18BuildOverrides.h`, the launcher applies it to both sketch and Arduino core C++ compilation. Custom uses this to disable the Zero USB activity LEDs on conflicting peripheral pins. Copy the header with that sketch; a plain Arduino IDE build does not apply the override.
