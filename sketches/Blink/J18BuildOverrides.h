#pragma once
// Required for the Arduino Zero compatibility profile to suppress core USB LED
// activity on board-specific pins, even with a custom bootloader. A matching
// custom profile already disables these LEDs, so repeating this is harmless.
// Launchers force-include this in core C++ files as well as the sketch.
// BuildSelection.h separately identifies the selected loader for verification;
// clocks and SWD/crystal pin reservations belong to the board profile.
#include <Arduino.h>
#undef PIN_LED_TXL
#undef PIN_LED_RXL
#define J18_CORE_USB_LEDS_DISABLED 1
