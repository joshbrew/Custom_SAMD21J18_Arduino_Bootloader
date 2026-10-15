# Windows SAMD21J18 build and flash tools

For the complete repository, open **`../../SAMD21_Custom_Bringup.cmd`** instead of
copying tools. Its menu provides four clearly labelled flash modes, sketch
selection, **P** for Zero Native USB / Custom J18 board profiles, and **B** for
bootloader choice. **T** changes actions to build/check only; **R** explicitly
recompiles without flashing. See [the menu guide](../../docs/BUILD_AND_FLASH_MENU.md).
Changing a board profile automatically recompiles an existing app on its next
sketch action. **D** runs the SWD diagnostic routine; see the repository guide.
**Q** installs/updates a custom profile into Arduino's sketchbook, with a
preview in BUILD/CHECK mode. See [profile installation](../../docs/ARDUINO_PROFILE_INSTALL.md).

Copy the **whole `Windows_J18_Tools` folder** into a sketch folder, then double-click a batch inside it. Both batches search **their own folder first**, then the **parent folder**, for `.ino` files. You can also keep the batches directly beside the `.ino`. If both locations contain sketches, the batch's own folder wins; ambiguous sketches stop with an error. Connect the ST-LINK to USB and power the target board before running a normal flash.

## Choose a workflow

| File | What it flashes |
|---|---|
| `build_and_flash_j18.bat` | One combined bootloader-plus-sketch image at `0x00000000`. It uses the official Arduino Zero bootloader from the selected installed Arduino SAMD core. |
| `build_and_flash_j18_separate_bootloader.bat` | The standalone `samd21_sam_ba.bin` at `0x00000000`, then the stored or newly compiled sketch-only binary at `0x00002000`, in separate OpenOCD runs. |

Double-click the desired batch file. Both workflows compile for **Arduino Zero (Native USB)** and use ST-LINK over SWD. Neither requires a reset wire or a separate `.cfg` supplied by you.

## What happens

1. Find Arduino CLI and OpenOCD in PATH, common installation folders, or the tool cache. Arduino IDE's bundled CLI can be reused.
2. Download SHA256-checked portable tools when missing, or replace unsupported old versions. Portable downloads go under `%LOCALAPPDATA%\SAMD21Custom\Tools`; no permanent PATH edit is required.
3. For a normal flash, check whether Windows detects the ST-LINK and whether its device/driver status reports a problem. A disconnected adapter is caught before compilation. If Windows reports a missing driver (code 28), install a matching signed ST-LINK driver, then recheck the device.
4. Install Arduino SAMD board support if missing.
5. Check build settings. The separate launcher uses the static sketch image or prints **Compiling sketch...** when it is missing or `--rebuild` is requested. The combined launcher retains its existing build messages. Tool output appears as it is produced.
6. Validate firmware sizes and vector tables, flash, verify, restore 8 KB bootloader protection, and start the sketch.

Builds and local bootloader/library searches use the selected sketch folder. The separate launcher uses the same `.j18-build` folder every time; the combined launcher retains its per-run folders. Firmware, generated configuration and the log stay in that run's folder.

## One persistent build folder

The separate-bootloader launcher uses **one static `.j18-build` folder** beside
the sketch. Normal runs use these files directly:

```text
.j18-build/
  firmware/
    samd21_sam_ba.bin       selected bootloader at 0x00000000
    firmware.sketch.bin    compiled sketch at 0x00002000
    flash_separate.cfg
  sketch-build.json
  build-and-flash.log
  compiler/                fixed compiler scratch; created when compiling
  compiler-output/         fixed compile output; created when compiling
  sketch/                  fixed staged sources; created when compiling
```

**No new timestamp folders are created.** The launcher compiles only when the
static sketch image is missing, the board profile differs, or you explicitly use `--rebuild`. Source,
header/library or tool changes do not automatically replace the stored image.
Normal runs skip Arduino compilation/settings discovery entirely. Image sizes,
vectors and displayed SHA256 values are checked for the files being flashed.

To try another bootloader, replace **`.j18-build/firmware/samd21_sam_ba.bin`**
while the launcher is closed, then rerun. That file takes priority and is not
automatically overwritten from the sketch folder, old builds or the Internet.
You can similarly replace `firmware.sketch.bin` with a valid sketch-only image
linked at 0x2000. A combined `.with_bootloader.bin` is not a sketch-only image.

```powershell
.\build_and_flash_j18_separate_bootloader.bat --rebuild
.\build_and_flash_j18_separate_bootloader.bat --build-only --rebuild
```

`--rebuild` recompiles into the same folder and keeps your selected bootloader.
Compiler/staging scratch is cleared so removed source files cannot persist.
The last published sketch is replaced only after compilation/validation succeeds;
a failed rebuild stops the current run and retains that last successful image.
To start completely fresh, delete `.j18-build` before running again. Deleting
just `firmware.sketch.bin` after initial setup also triggers compilation.
The latest run overwrites the same log/config files. A lock prevents two
launchers from changing this build folder simultaneously.

On upgrade, the launcher can import the latest valid recorded compilation from
an old dated folder without recompiling. It retains those old folders and stops
creating new ones; after confirming the static files, the old dated folders can
be removed manually. The combined-image batch retains its existing workflow.

The separate launcher selects native ST-LINK with its required USB discovery IDs
and modern `swd`, with a transport fallback for older OpenOCD builds.

## Local standalone bootloader

The separate-bootloader batch searches in this order:

1. `.j18-build/firmware/samd21_sam_ba.bin` — authoritative once present.
2. `samd21_sam_ba.bin` beside the sketch.
3. The sketch's `build` folder, including subfolders.
4. The bootloader paired with a dated build being imported during initial migration.
5. Download the [official Arduino Zero bootloader](https://github.com/arduino/ArduinoCore-samd/blob/master/bootloaders/zero/samd21_sam_ba.bin).

The selected path and SHA256 are printed. If a build folder contains different bootloader files, place the intended copy beside the sketch to make the selection explicit.

## Check or compile without flashing

Run these from the tools folder in PowerShell or Command Prompt (or prefix each path with `Windows_J18_Tools\` when running from the sketch folder):

```powershell
.\build_and_flash_j18.bat --check
.\build_and_flash_j18.bat --build-only
.\build_and_flash_j18_separate_bootloader.bat --check
.\build_and_flash_j18_separate_bootloader.bat --build-only
```

`--check` reports tool versions and Windows USB status without installing, compiling or contacting the target. `--build-only` may install missing tools/core support, then compiles and checks the OpenOCD configuration without flashing. It does not require a connected ST-LINK.

## Wiring and USB driver

| ST-LINK | SAMD21 target |
|---|---|
| SWDIO | PA31 / SWDIO |
| SWCLK | PA30 / SWCLK |
| GND | GND |
| VTref, if available | Target 3.3 V reference |

Use the adapter's actual pinout. A 3.3 V power output is not necessarily a VTref input. Power the board normally; RESET must idle high.

The USB check can identify an absent ST-LINK, a missing driver, or a disabled/problem device. Windows detection alone cannot prove that OpenOCD can open the debug interface: another application can hold the adapter, and driver bindings or adapter firmware can still be incompatible.

On a normal flash run, **Windows device error code 28 (driver missing)** triggers automatic installation. The batch searches an extracted `stsw-link009` folder beside the batch or sketch, its driver cache, and OpenOCD's `drivers/ST-Link` folder. It selects driver INFs matching the connected ST-LINK hardware and checks their signed catalogs. If none match, it uses `stsw-link009.zip` or `en.stsw-link009.zip` beside the sketch, or tries downloading the [official STSW-LINK009 package](https://www.st.com/en/development-tools/stsw-link009.html#get-software).

**Approve the Windows administrator (UAC) prompt** to install the driver. Windows PnPUtil verifies the package files against the signed catalog and installs matching drivers. The batch rechecks the device before continuing to compilation. It does not force replacement of a working driver or automatically reboot. If a restart is required, restart Windows and rerun. Installation logs are under `%LOCALAPPDATA%\SAMD21Custom\Tools\stsw-link009`.

If ST's download is unavailable or requires a browser step, the batch stops and prints the download page. Download and extract the package into `stsw-link009` beside the batch (or place its ZIP beside it), then rerun. A disconnected adapter or other Windows device error does not trigger installation. `--check` and `--build-only` never install USB drivers. Automatic installation currently supports x64 Windows.

If OpenOCD reports **Error: open failed**, check the USB cable/connection, close STM32CubeProgrammer or other debugging software, and inspect Device Manager. A healthy Windows device entry alone does not guarantee that OpenOCD can access the USB interface.

The native ST-LINK driver requires supported adapter firmware (ST-LINK/V2 V2J24 or newer). Update firmware with ST's tools if necessary.

## Sketch layout and libraries

A normal Arduino sketch folder has a main `.ino` with the same name as the folder. The separate launcher always stages nearby source files plus `src` and `data` into a correctly named build folder, including for a single differently named `.ino`. This prevents Arduino from recursively copying old build folders. Keep relative includes within the staged sources or use an explicitly supported library location.

Project-specific libraries must already be available to Arduino CLI. A local `libraries` subfolder is included when present.

Compilation/configuration checks have been tested on Windows. A successful flash must complete verification and return exit code zero; test the resulting application on the board.

## Sketch-specific core overrides

If the selected sketch contains `J18BuildOverrides.h`, the launcher applies it to both sketch and Arduino core C++ compilation. Custom uses this to disable the Zero USB activity LEDs on conflicting peripheral pins. Copy the header with that sketch; a plain Arduino IDE build does not apply the override.
