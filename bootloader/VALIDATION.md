# Validation scope

Rebuild using `python build.py --verify-repeat`. This checks pinned upstream
hashes, identical repeated BIN bytes, boot vectors/capacity, the 0x2000 app
boundary, generated profile hashes and matching clock/pin-state configuration.
Run `python ../tools/run_checks.py` for menu/cache/profile/SWD and GPIO tests.
Compile `Blink` with both the custom and Arduino Zero profiles before packaging.

These checks do not establish physical USB, oscillator, wiring or peripheral
operation. The generic board configuration has not been tested on hardware.
SWD image comparisons establish byte matches, while startup checkpoints require
matching application/ELF symbols. Reset recovery requires a working NRST wire.
