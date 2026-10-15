# Configurable SAMD21J18A SAM-BA bootloader

The pinned Arduino SAMD core 1.8.14 bootloader is adapted for a configurable J18
board, native USB CDC/SAM-BA, an 8 KB boot region and a matching Arduino variant.
Edit board.json; generated files should not be edited directly.

```text
python build.py --verify-repeat
python ../tools/arduino_profile_workflow.py --build --install --export
```

The first command builds only. The second explicitly installs into the Arduino
sketchbook and exports a portable profile. FQBN: `custom:samd:custom_j18`.
Menu L instead isolates settings/firmware/profile inside the selected sketch's
.j18-build/custom-bootloader folder. Menu G rebuilds repository defaults.

Internal/crystal-less is default; external mode requires PA00/PA01 crystal
hardware. Both loader and application profile use the selected clock mode.
The boot indicator is disabled by default. Interface aliases default to input
with no pulls. Configure safe outputs only after reviewing actual wiring.
The alias schema permits additional GPIO names with explicit boot states.
UART/I2C/SPI drivers accept the documented, reviewed routes; changing a pin
name alone cannot change the SERCOM pad multiplexer in silicon.

Flash loader at 0x00000000 and application at 0x00002000. The linker reserves
the double-tap reset cell at 0x20007FFC and keeps the initial app stack aligned
to 8 bytes. PA30/PA31 SWD and PA00/PA01 crystal entries are excluded from the
Arduino startup GPIO sweep. USB PA24/PA25 are reserved from configurable aliases.
The Zero compatibility profile has its own pins/clock settings; flashing this
loader does not change those application settings.

Generated artifacts include the BIN/HEX/ELF, symbols, manifests, change patch,
identity header and Arduino profile. A repeat build must produce identical BIN
bytes. The upstream source and SHA256 records remain unchanged. The optional
boot LED is active HIGH and needs a series resistor. USB IDs use Arduino Zero
development values until configured; they are not allocated to this project.
No generic board configuration has been physically tested by this cleanup.
