// Custom adaptation of Arduino Zero definitions; LGPL-2.1-or-later.
#include "board_definitions_arduino_zero.h"
#include "custom_board.h"
#undef STRING_PRODUCT
#undef STRING_MANUFACTURER
#undef USB_VID_HIGH
#undef USB_VID_LOW
#undef USB_PID_HIGH
#undef USB_PID_LOW
#undef BOARD_LED_PORT
#undef BOARD_LED_PIN
#undef BOARD_LEDRX_PORT
#undef BOARD_LEDRX_PIN
#undef BOARD_LEDTX_PORT
#undef BOARD_LEDTX_PIN
#define STRING_PRODUCT "Custom SAMD21 Boot"
#define STRING_MANUFACTURER "Custom"
#define USB_VID_HIGH 0x23
#define USB_VID_LOW 0x41
#define USB_PID_HIGH 0x00
#define USB_PID_LOW 0x4d
#if J18_BOOT_INDICATOR
#define BOARD_LED_PORT (J18_LED / 32)
#define BOARD_LED_PIN (J18_LED % 32)
#endif
