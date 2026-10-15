# Official loader test fixture

ArduinoCore-samd 1.8.14 `bootloaders/zero/samd21_sam_ba.bin`, verified against:
https://raw.githubusercontent.com/arduino/ArduinoCore-samd/1.8.14/bootloaders/zero/samd21_sam_ba.bin

6,504 bytes; CRC32 `032dc51e`; SHA256
`89b9255d2f0bfa90371772b4e2eff78aa6069c6e612eb35737e964074ad8512b`.
This binary starts its MSP at `0x20007FFC`; compatibility file checks accept
word alignment for a bootloader while requiring 8-byte application alignment.
Arduino source/notices are in ../../upstream and ../../LICENSE.
