# Custom SAMD21 Arduino Bootloader maintenance

- This repository is generic. Do not import system-specific sketches, bridge
  firmware, BOMs, bench reports, or peripheral power-control defaults.
- Configuration source: bootloader/board.json. The supported custom target is
  SAMD21J18A; supporting another MCU needs reviewed linker/startup/variant work.
- Keep the 8 KB loader, app at 0x2000, double-tap cell and 8-byte app stack alignment.
- Reserve PA00/PA01 for the crystal, PA24/PA25 for USB and PA30/PA31 for SWD.
- Default boot indicator is disabled; interface pins default to input/no pulls.
- Menus configure sketch-local loaders/profiles under .j18-build/custom-bootloader.
  Isolated builds must not overwrite repository defaults. Keep clock settings in
  both the loader and matching application profile.
- Preserve upstream bytes, SHA256 provenance, original licenses and notices.
- Run python tools/run_checks.py. After loader/generator changes, rebuild using
  python bootloader/build.py --verify-repeat and compile Blink for custom and Zero.
- Builds and tests do not flash, install globally, publish, or push unless requested.
- Keep scratch files ignored, manifests accurate, and hardware testing separate
  from host/build checks. No generic hardware configuration is bench-certified.
