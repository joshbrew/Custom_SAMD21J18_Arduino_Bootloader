# SWD probe and startup walkthrough

Open your platform's top-level Custom menu, select a sketch with **S**, then
press **D**. The probe uses the binaries already saved for that sketch; it does
not compile, select another bootloader, erase flash, program flash, or change
fuses. The board-profile selector does not change what the probe inspects.

Choose:

1. **Full probe:** halt/read the SAMD21, inspect flash, perform one software
   reset, and walk through startup using hardware breakpoints.
2. **Inspect only:** halt/read/resume, without resetting or walking startup.
3. **General SAMD21 inspection:** halt/read/resume using raw PA/PB register
   names, with no local binary/ELF comparison and no assumed boot/app layout.
   Use this when switching between Custom and a Feather M0 or another board.
4. **ST-LINK recovery under reset:** connect the adapter's actual **NRST/SRST
   output to the SAMD RESET pin** first, keeping SWDIO/SWCLK/GND connected and
   the target powered. This explicitly asserts hardware reset during connection,
   then resets/halts, performs generic inspection without local comparisons and
   resumes. It does not flash. It is available only with ST-LINK; Pi GPIO reset
   wiring is not configured. Do not select it with the reset wire absent.
5. **Full probe with wired ST-LINK reset:** uses the same NRST connection as 4,
   compares the selected saved boot/app images, captures exception frames, then
   walks startup with matching ELF symbols. Use this when normal attachment fails
   and you need more than generic recovery inspection. No flashing.
6. **Identify installed images:** searches local compiled binaries, reads the
   relevant flash range once, and reports complete binary-range matches for boot
   and application separately. Choose ordinary connection or wired ST-LINK reset.
   This does not change the selected sketch/loader or walk startup.

With **T → BUILD/CHECK ONLY**, any choice prepares and validates the probe
files without opening the adapter. Windows needs Python 3.10+ for this feature
(`py -3`, `python`, or `J18_PYTHON`); the Unix menus already use Python.

## What it checks

Image identification searches `bootloader/firmware`, configured bootloader outputs
and static sketch images under `sketches`, Arduino `build/` exports, reviewed
`releases`, bundled official loader copies, and installed Arduino SAMD core
bootloader binaries. Remembered local-loader paths and selected compatible source
folders are also considered. It does not download files or compile anything.
Unpublished compiler scratch outputs and non-firmware data files are excluded.

The scan only reads through the highest candidate image end, bounded by detected
flash capacity. Reading may take longer than an ordinary probe while the CPU is
paused. The resulting `image-catalog.json`, `installed-flash.bin`, and matches in
`swd-report.json` stay in the selected sketch's static `.j18-build/swd` folder.
Identical binaries can have several filenames/profiles; all aliases remain in the
report. A complete candidate byte range may match while bytes outside that range
remain unidentified. A match identifies installed bytes, not which image is
currently executing or whether the physical wiring matches its profile.

A HardFault captured during reset recovery is preserved as a failure even if
image identity is confirmed. Use routine 5 to verify the selected images and
relate the captured fault frame to the matching ELF before attributing the fault
to normal sketch execution.

If connection fails with `init mode failed (unable to connect to the target)`,
ST-LINK may still report a target voltage while being unable to talk to the SAMD
debug port. No CPU, installed-flash or clock inspection has occurred at that point.
The probe enables OpenOCD debug-level 3 logging. The report preserves the adapter
error, voltage, firmware identity and low-level ST-LINK/USB error messages when
the driver provides them; the complete raw trace is in `swd-probe.log`. Debug
echo traces are excluded from result parsing, so they cannot create duplicate
PASS/FAIL checks. A status such as `STLINK_SWD_DP_ERROR` narrows the failing
operation but does not uniquely distinguish wiring, timing or adapter firmware.
Close other probe sessions,
power-cycle the target, reconnect ST-LINK and try D → 3. Check PA31/SWDIO,
PA30/SWCLK, ground and target supply. A wired RESET/NRST connection with a matching
connect-under-reset configuration can help persistent recovery failures; the
normal routines 1–3 use no reset wire. Routines 4/5 and the wired choice in 6
enable recovery explicitly.
It requires `reset_config srst_only srst_nogate srst_open_drain connect_assert_srst`
as described in the [OpenOCD reset configuration guide](https://openocd.org/doc/html/Reset-Configuration.html).
If initialization fails, execution-stage adapter commands are unavailable: the
report marks reset release **SKIP**, asking you to disconnect NRST and power-cycle.
After successful initialization, later recovery errors trigger an attempted reset
release. If release fails, disconnect NRST and power-cycle before continuing.
Physical reset-line operation still needs bench validation with the actual adapter.

Older custom Custom Arduino profiles let the core's startup GPIO sweep clear
PA30/PA31's SWD mux. This was corrected in the generator on October 14, 2026;
PA00/PA01 crystal pins are also protected. Rebuild the profile and sketch, not
only the bootloader. If the old sketch is already running and blocks connection,
double-tap and fully release RESET within 0.5 seconds to request the boot monitor,
then use menu action 4 to flash the corrected sketch. Do not hold RESET down
during a normal no-reset probe/flash: CPU examination may read CPUID 0. See
[the profile defect and recovery notes](HARDWARE_ISSUES.md#custom-arduino-profile-startup-disabled-swd-fixed-october-14-2026).

Confirm the connector for the actual adapter model. The official ST-LINK/V2
20-pin ARM connector has NRST on pin 15; its separate 4-pin STM8/SWIM connector
has a different reset signal ([ST UM1075, tables 2 and 4](https://www.st.com/resource/en/user_manual/um1075-stsw-link007.pdf)).
Those pin numbers do not apply to an unidentified 10-pin dongle or to a different
ST-LINK model. A dongle's `RST` label alone does not establish ARM reset control.
Measure at SAMD RESET: it should idle high and be pulled low by the adapter
during an effective reset assertion. A scope can catch a short assertion that
a multimeter misses. If the sketch keeps blinking throughout recovery, reset
operation has not been demonstrated. Avoid attributing an initialization failure
to the installed binary: no erase, write or image comparison has happened yet.

| Check | Evidence |
| --- | --- |
| SWD connection | OpenOCD connects to the debug port and halts the CPU. |
| CPU/chip identity | Auto-detects supported SAMD21 E/G/J parts (including Feather M0 G18 and Custom J18) from DSU DID, independently of silicon revision. Confirms flash geometry with NVMCTRL. |
| CPU state | PC/LR/MSP/PSP/xPSR/CONTROL, active exception, vector table location, reset-cause register, and up to 16 stack words. For HardFault, a valid EXC_RETURN selects the MSP/PSP exception frame; stacked PC/LR and the active exception vector are recorded within detected memory bounds. |
| GPIO/pin mux | PORTA/PORTB direction/output/input and raw pin config/mux positions. Optional Custom aliases use the configuration saved with the compiled/imported sketch; older receipts fall back to `bootloader/board.json`. They are not applied to a detected G/E package. Raw register positions can include unbonded pins. |
| Clock/SERCOM state | APBA/APBB/APBC masks; SYSCTRL oscillator/DFLL status, external 32.768 kHz enable/ready, GCLK status, and enabled SERCOM CTRLA/CTRLB/BAUD. Disabled controllers are skipped. These are snapshots, not successful bus-transaction tests. |
| Flash vectors | Default boot/app split is `0x2000`; configurable for other layouts, including app at zero. Stack/flash bounds use the detected device's actual RAM/flash sizes. General inspection skips layout assumptions. |
| Boot protection | Reads/decodes BOOTPROT. Reports the observed region; checks a specific size only when `--expected-boot-bytes` is supplied. Does not change fuses. |
| Stored images | `verify_image` compares installed flash against frozen copies of the static bootloader/app, when available. A mismatch is reported without reflashing. |
| Image identification (6) | Complete binary-range SHA256 matches against local bootloaders, sketch binaries and the boot/app portions of combined exports. All matching aliases are retained; no match means unknown, not guessed firmware. |
| Startup reset | Normal full mode requests SYSRESETREQ; wired full mode uses the configured reset connection. |
| App entry | A hardware breakpoint checks whether the bootloader reaches the installed application's reset handler. |
| Arduino startup | With matching ELF symbols, checks whether execution reaches `setup()`, `loop()`, and `loop()` again. |
| Cleanup | Removes the probe's breakpoint and resumes the CPU without another reset. |

The named checkpoints are enabled only when the retained `firmware.sketch.elf`
or an ELF under `compiler-output/`
reconstructs **exactly the saved application binary**, and that application
verifies against the installed flash. A manually swapped app without matching
symbols still gets chip/CPU/flash/vector checks and the app-entry check. Missing
images or symbols produce **SKIP**, not a claim that those checks passed.

Each startup checkpoint waits up to 12 seconds by default. A timeout can mean a
bootloader waiting for upload, a deliberate USB/client wait, a fault, a watchdog
reset, or a slow initialization. It does not identify the cause by itself.
Repeated rapid resets can intentionally select the bootloader's double-tap
mode; wait or power-cycle before retrying. The probe does not overwrite the
double-tap RAM marker to force a startup result.

Device identities and memory sizes live in `tools/swd_probe_devices.json`, based
on the [OpenOCD SAMD device table](https://github.com/openocd-org/openocd/blob/v0.12.0/src/flash/nor/at91samd.c).
Known SAMD21 A/B/D/L/CU/BU variants are supported. Other MCU families require a
reviewed register backend, not just a renamed chip label. The DID identifies
the chip, not the physical Feather/Custom PCB. `--board-map auto` uses Custom
aliases only for a saved Custom profile on a compatible J package; otherwise
it uses generic register names. Flashing profiles and target programming are
unchanged by auto-detection in this diagnostic routine.

## Reading failures

**Image mismatch:** the saved sketch and installed firmware differ, or the
read failed. A stack of `0x20007FF8` on the board versus `0x20008000` in the file
is consistent with Custom versus Zero linker layouts. It does not by itself
mean corrupt flash. Choose the intended sketch/profile and flash that image to
compare it, or use D → 3 to inspect without local image expectations. Named
checkpoints are intentionally skipped when the selected ELF does not describe
the installed bytes.

**Timeout before setup:** when the installed image matches the ELF, the report
resolves captured PCs to function ranges. If a failed checkpoint is in
`SystemInit` with XOSC32K enabled but not ready, it specifically reports the
external 32.768 kHz oscillator wait. Check crystal presence,
connections and load capacitors. If the board intentionally has no crystal,
use a reviewed crystal-less profile; `bootloader/board.json` has a `crystalless`
setting, followed by L to regenerate/rebuild and R to recompile the sketch.
The repository default is an example crystal-less configuration; an external-
crystal preset is also supplied. Neither is hardware-certified here. These
diagnostics do not rewrite clock registers or hide a genuine startup failure.

## Files and interpretation

One fixed folder is reused:

```text
sketches/<selected sketch>/.j18-build/swd/
  swd-report.json            PASS / FAIL / SKIP checks and captured values
  swd-probe.log              Complete OpenOCD output and CPU snapshots
  swd_probe.cfg              Generated diagnostic configuration
  boot-vectors.bin           First 64 bytes of the installed boot region
  app-vectors.bin            First 64 bytes of the installed application
  expected-*.bin             Frozen local comparison images, when available
```

The report/log are replaced on each run. Copy them elsewhere to keep history.
`CONFIGURATION_ONLY` means no hardware was accessed. A hardware report's PASS
status applies to the checks actually performed; review any SKIP entries.
If OpenOCD loses the connection or exceeds the overall time limit, cleanup may
not succeed and the CPU state may be uncertain. Reconnect or power-cycle before
continuing. The diagnostic timeout/error is recorded rather than called PASS.

Pausing the CPU affects timing: USB and UART transactions can
time out. Full mode also executes the currently installed application, including
whatever module/power actions that firmware normally performs. Inspect-only
avoids the startup reset, but still interrupts execution briefly. The probe
does not drive test patterns, transmit radio traffic, or rewrite peripheral
configuration. GPIO snapshots show digital states, not measured voltage or
signal quality. Bus transactions, external module functionality and electrical
behavior remain tests for the board-specific test firmware and bench tools.

## Reset wiring

**A reset wire is not required for this routine.** It uses:

```tcl
reset_config none
cortex_m reset_config sysresetreq
```

ST-LINK: SWDIO → SAMD PA31, SWCLK → PA30, common GND; target powered normally.
The target's RESET input must idle high at its 3.3 V logic level. A probe's
dedicated NRST/SRST connection can help recover a target that cannot respond
to software reset, but using it requires a corresponding OpenOCD reset
configuration. These scripts deliberately retain the reset-free configuration.
If you add that wire, connect it to the **SAMD21 RESET** signal, not the Nordic
module's `GPIO_RST` alias. Never connect RESET to 5 V.

Pi 4 GPIO mode uses BCM8 SWDIO / BCM11 SWCLK with no reset GPIO. Other Linux
machines and macOS use ST-LINK. Choose **A** in the Linux menu to select the
adapter; **U** controls sudo for ST-LINK. GPIO access elevates only OpenOCD.

The reset mechanism and debugging commands follow the
[OpenOCD Cortex-M commands](https://openocd.org/doc/html/Architecture-and-Core-Commands.html)
and [memory/breakpoint commands](https://openocd.org/doc-release/html/General-Commands.html).

## Command-line examples

Prepare/check without hardware access:

```powershell
.\SAMD21_Custom_Bringup.cmd -Mode probe -Sketch Blink -BuildOnly
```

```sh
bash SAMD21_Custom_Bringup_Linux.sh --mode probe --sketch Blink --build-only --adapter stlink
./SAMD21_Custom_Bringup_Mac.command --mode probe --sketch Blink --build-only
```

Run the actual startup probe, without flashing:

```powershell
.\SAMD21_Custom_Bringup.cmd -Mode probe -Sketch Blink
.\SAMD21_Custom_Bringup.cmd -Mode probe -Sketch Blink -InspectOnly
```

```sh
bash SAMD21_Custom_Bringup_Linux.sh --mode probe --adapter stlink
bash SAMD21_Custom_Bringup_Linux.sh --mode probe --inspect-only --adapter stlink
```

General inspection and alternate layouts are also available from commands:

```powershell
.\SAMD21_Custom_Bringup.cmd -Mode probe -InspectOnly -ProbeNoCompare
.\SAMD21_Custom_Bringup.cmd -Mode probe -ProbeAppOffset 0x1000 -ProbeMap generic
```

```sh
bash SAMD21_Custom_Bringup_Linux.sh --mode probe --inspect-only --probe-no-compare
python3 tools/swd_probe.py --inspect-only --no-compare --board-map generic
python3 tools/swd_probe.py --app-offset 0 --no-compare
```

Advanced options are available through `python tools/swd_probe.py --help`,
including `--timeout-ms`, `--expected-boot-bytes`, and `--leave-halted`. This automated routine closes
OpenOCD afterward; it is not an interactive GDB session.
