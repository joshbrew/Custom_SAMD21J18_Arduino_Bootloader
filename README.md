# Custom SAMD21 Arduino Bootloader

Configure a SAM-BA bootloader and matching Arduino board profile, compile a
sketch, flash with OpenOCD, and inspect a target over SWD. Windows, Linux /
Raspberry Pi and macOS launchers use the same configurable workflow.

The included custom target is **SAMD21J18A**, with 256 KB flash, 32 KB RAM and
an 8 KB boot region. Arduino Zero Native USB is also a selectable compilation
profile. SWD inspection supports additional SAMD21 E/G/J device identities;
that does not make the custom J18 variant portable to other packages unchanged.

## Start

- Windows: `SAMD21_Custom_Bringup.cmd`
- Linux / Raspberry Pi: `bash SAMD21_Custom_Bringup_Linux.sh`
- macOS: `./SAMD21_Custom_Bringup_Mac.command`

Select **S** sketch, **P** board profile and **B** bootloader. The only included
sketch is the standard `Blink` example, which uses the selected profile's
`LED_BUILTIN`. The custom example maps it to PA05; use an LED and series resistor
or edit the alias. No external module is assumed or powered by this workflow.

Flash actions 1–4 are combined profile loader + sketch, selected separate loader
+ sketch, loader only, and sketch only. **T** switches to build/check only.
**R** rebuilds the selected sketch. **L** configures and builds a sketch-local
custom loader/profile; **G** rebuilds the repository defaults. **Q** installs a
profile, and **E** builds/installs/exports a profile. **D** inspects over SWD.
Sketches reuse one static `.j18-build/firmware` directory. Settings are remembered.

## Configuration

Edit `bootloader/board.json` or use menu L. Internal/crystal-less mode is the
default. External mode requires a 32.768 kHz crystal on PA00/PA01. The boot LED
is disabled and all configured interface pins start as inputs without pulls.
Review wiring before enabling indicators or driving GPIO. PA00/PA01, PA24/PA25
and PA30/PA31 stay reserved for crystal, USB and SWD.

The supplied UART, I2C and SPI aliases are example SERCOM routes, not a PCB
pinout. UART0 uses PA22/PA23, UART1 PA16/PA17, Wire PA08/PA09, SPI0
PB12/PB13/PB11 and SPI1 PB16/PB17/PB22. The generator rejects unreviewed routes.
Additional ordinary GPIO aliases can be added with explicit boot states.
USB IDs inherit Arduino Zero development defaults; they are not an allocation
of identifiers for a new product. Choose appropriate IDs before distribution.

Requires Python 3.10+, Arduino CLI / SAMD core 1.8.14, and OpenOCD 0.12+ for SWD.
The platform launchers can locate/install their supported tools. No command
below flashes or installs globally:

```text
python bootloader/build.py --verify-repeat
python tools/build_samd.py blink --board custom --bootloader custom
python tools/build_samd.py blink --board zero --bootloader official
python tools/arduino_profile_workflow.py --export
python tools/run_checks.py
python tools/package_repo.py
```

Use `python3` on Unix or `py -3` on Windows if needed. To build, install and export
in one explicit operation: `python tools/arduino_profile_workflow.py --build --install --export`.
The generated FQBN is `custom:samd:custom_j18`.

See [menu details](docs/BUILD_AND_FLASH_MENU.md), [SWD inspection](docs/SWD_PROBE.md),
[profile installation](docs/ARDUINO_PROFILE_INSTALL.md),
[profile distribution](docs/ARDUINO_PROFILE_RELEASE.md), and
[bootloader details](bootloader/README.md). Preserve the upstream LGPL notices;
see [source licenses](docs/LICENSES.md). Builds are verified on the host;
the generic default configuration has not been tested on physical hardware.
