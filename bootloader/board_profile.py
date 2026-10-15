"""Generate a local Arduino platform using the installed Arduino SAMD core."""
import json
import re
import shutil
from build import ROOT, VALID, write


def validate_routes(b):
    pins = b['pins']
    # Fail explicitly when a future PCB changes a SERCOM route we have not reviewed.
    if (pins['UART0_TX'], pins['UART0_RX']) != ('PA22', 'PA23'):
        raise ValueError('Update/review UART0 SERCOM/pads in board_profile.py for this revision.')
    if pins['UART1_TX'] != 'PA16' or pins['UART1_RX'] not in ('PA17', 'PA18'):
        raise ValueError('Update/review UART1 SERCOM/pads in board_profile.py for this revision.')
    if (pins['SDA'], pins['SCL']) != ('PA08', 'PA09'):
        raise ValueError('Update/review Wire SERCOM/pads in board_profile.py for this revision.')
    spi0 = tuple(pins[k] for k in ('SPI0_MOSI', 'SPI0_SCK', 'SPI0_MISO'))
    spi1 = tuple(pins[k] for k in ('SPI1_MOSI', 'SPI1_SCK', 'SPI1_MISO'))
    if spi0 not in (('PB12', 'PB10', 'PB11'), ('PB12', 'PB13', 'PB11')):
        raise ValueError('Update/review SPI0 SERCOM/pads in board_profile.py for this revision.')
    if spi1 not in (('PB22', 'PB16', 'PB17'), ('PB16', 'PB17', 'PB22')):
        raise ValueError('Update/review SPI1 SERCOM/pads in board_profile.py for this revision.')
    spi0_ok = spi0 == ('PB12', 'PB13', 'PB11')
    spi1_ok = spi1 == ('PB16', 'PB17', 'PB22')
    if spi1_ok and not spi0_ok:
        raise ValueError('SPI1-only revision is not supported by the standard SPI/SPI1 objects; review the variant first.')
    return spi0_ok, spi1_ok


def pin_type(pin, mux):
    # Arduino init() calls pinMode(INPUT) for every NUM_DIGITAL_PINS entry.
    # pinMode clears PINCFG.PMUXEN unless this sentinel makes it return early.
    # Keep table indices/aliases stable, but never reclaim SWD or the crystal.
    if pin in ('PA00', 'PA01', 'PA30', 'PA31'):
        return 'PIO_NOT_A_PIN'
    return mux.get(pin, 'PIO_DIGITAL')


def generate_profile(b, package, firmware, destination=None, aliases_out=None):
    pins = b['pins']
    spi0_ok, spi1_ok = validate_routes(b)
    core = package / 'hardware/samd/1.8.14'
    if not core.is_dir():
        raise FileNotFoundError('The profile requires installed arduino:samd@1.8.14.')
    dest = destination or ROOT / 'arduino-profile/hardware/custom/samd'
    variant = dest / 'variants/custom_j18'
    variant.mkdir(parents=True, exist_ok=True)
    # Put A0..A19 first: the stock core treats analogRead(0..A0-1) specially.
    analog = ['PA02', 'PA03', 'PB08', 'PB09', 'PA04', 'PA05', 'PA06', 'PA07',
              'PB00', 'PB01', 'PB02', 'PB03', 'PB04', 'PB05', 'PB06', 'PB07',
              'PA08', 'PA09', 'PA10', 'PA11']
    order = analog + [p for p in VALID if p not in analog]
    index = {p: i for i, p in enumerate(order)}
    pio = (package / 'tools/CMSIS-Atmel/1.2.0/CMSIS/Device/ATMEL/samd21/include/pio/samd21j18a.h').read_text()
    eic = {m[0]: ('EXTERNAL_INT_NMI' if m[1] == 'NMI' else 'EXTERNAL_INT_' + m[1][6:])
           for m in re.findall(r'#define PIN_(P[AB]\d\d)A_EIC_(EXTINT\d+|NMI)\s', pio)}
    mux = {p: 'PIO_SERCOM' for p in ['PA08', 'PA09', 'PA16', 'PA17', 'PA18',
                                   'PA22', 'PA23', 'PB12', 'PB13', 'PB16', 'PB17']}
    mux.update({p: 'PIO_SERCOM_ALT' for p in ['PB10', 'PB11', 'PB22']})
    h = '// Generated from board.json; LGPL-2.1-or-later. Use PIN_* / J18_PIN_* in Arduino APIs.\n'
    h += '#pragma once\n#include "WVariant.h"\n#include "CustomBootloaderIdentity.h"\n'
    h += '#define ARDUINO_SAMD_VARIANT_COMPLIANCE 10610\n#define J18_CORE_USB_LEDS_DISABLED 1\n'
    h += '#define VARIANT_MAINOSC 32768ul\n#define VARIANT_MCK 48000000ul\n'
    h += f'#define PINS_COUNT {len(order)}u\n#define NUM_DIGITAL_PINS {len(order)}u\n'
    h += '#define NUM_ANALOG_INPUTS 20u\n#define NUM_ANALOG_OUTPUTS 1u\n#define ADC_RESOLUTION 12\n'
    h += '#define analogInputToDigitalPin(p) ((p) < 20u ? (p) : -1)\n'
    h += '#define digitalPinToPort(P) (&PORT->Group[g_APinDescription[P].ulPort])\n'
    h += '#define digitalPinToBitMask(P) (1ul << g_APinDescription[P].ulPin)\n'
    h += '#define portOutputRegister(p) (&(p)->OUT.reg)\n#define portInputRegister(p) (&(p)->IN.reg)\n#define portModeRegister(p) (&(p)->DIR.reg)\n'
    h += '#define digitalPinHasPWM(P) (g_APinDescription[P].ulPWMChannel != NOT_ON_PWM || g_APinDescription[P].ulTCChannel != NOT_ON_TIMER)\n'
    for p, i in index.items():
        h += f'#define J18_PIN_{p} {i}u\n'
    for name, p in pins.items():
        h += f'#define PIN_{name} J18_PIN_{p}\n'
    for i in range(20):
        h += f'#define PIN_A{i} {i}u\n#define A{i} PIN_A{i}\n'
    h += '#define PIN_DAC0 PIN_A0\n#define DAC0 PIN_DAC0\n'
    h += '#define LED_BUILTIN PIN_LED\n#define PIN_LED_13 PIN_LED\n'
    h += '#define SS PIN_CS0 // Default chip-select alias; configure it for the attached device.\n'
    h += '#define PIN_USB_DM J18_PIN_PA24\n#define PIN_USB_DP J18_PIN_PA25\n'
    h += '#define PIN_SERIAL1_TX PIN_UART0_TX\n#define PIN_SERIAL1_RX PIN_UART0_RX\n'
    h += '#define PIN_SERIAL2_TX PIN_UART1_TX\n#define PIN_SERIAL2_RX PIN_UART1_RX\n'
    h += '#define WIRE_INTERFACES_COUNT 1\n#define PIN_WIRE_SDA PIN_SDA\n#define PIN_WIRE_SCL PIN_SCL\n'
    h += '#define PERIPH_WIRE sercom0\n#define WIRE_IT_HANDLER SERCOM0_Handler\n#define SDA PIN_SDA\n#define SCL PIN_SCL\n'
    h += f'#define J18_HARDWARE_SPI0_VALID {int(spi0_ok)}\n#define J18_HARDWARE_SPI1_VALID {int(spi1_ok)}\n'
    h += f'#define SPI_INTERFACES_COUNT {0 if not spi0_ok else 2 if spi1_ok else 1}\n'
    # Only instantiate hardware SPI for a reviewed, valid SERCOM route.
    if spi0_ok:
        h += '#define PIN_SPI_MISO PIN_SPI0_MISO\n#define PIN_SPI_MOSI PIN_SPI0_MOSI\n#define PIN_SPI_SCK PIN_SPI0_SCK\n'
        h += '#define PERIPH_SPI sercom4\n#define PAD_SPI_TX SPI_PAD_0_SCK_1\n#define PAD_SPI_RX SERCOM_RX_PAD_3\n'
        h += '#define MISO PIN_SPI_MISO\n#define MOSI PIN_SPI_MOSI\n#define SCK PIN_SPI_SCK\n'
        if spi1_ok:
            h += '#define PERIPH_SPI1 sercom5\n#define PAD_SPI1_TX SPI_PAD_0_SCK_1\n#define PAD_SPI1_RX SERCOM_RX_PAD_2\n'
    h += '#define I2S_INTERFACES_COUNT 0\n#ifdef __cplusplus\n#include "SERCOM.h"\n#include "Uart.h"\n'
    h += ''.join(f'extern SERCOM sercom{i};\n' for i in range(6))
    h += 'extern Uart Serial1;\nextern Uart Serial2;\nextern "C" unsigned int PINCOUNT_fn();\n#endif\n'
    h += '#define Serial SerialUSB\n#define SerialUART0 Serial1\n#define SerialUART1 Serial2\n'
    h += '#define SERIAL_PORT_MONITOR SerialUSB\n#define SERIAL_PORT_USBVIRTUAL SerialUSB\n#define SERIAL_PORT_HARDWARE Serial1\n'
    # No HARDWARE_OPEN: UARTs are exposed as generic Serial1/Serial2 interfaces.
    write(variant / 'variant.h', h)
    cpp = '// Generated Custom variant; LGPL-2.1-or-later.\n#include "variant.h"\n#include "custom_board.h"\n'
    cpp += 'const PinDescription g_APinDescription[] = {\n'
    for p in order:
        adc = f'ADC_Channel{analog.index(p)}' if p in analog else 'No_ADC_Channel'
        attr = 'PIN_ATTR_DIGITAL|PIN_ATTR_ANALOG' if p in analog else 'PIN_ATTR_DIGITAL'
        kind = pin_type(p, mux)
        if kind == 'PIO_NOT_A_PIN':
            attr = 'PIN_ATTR_NONE'
        cpp += f'  {{PORT{p[1]}, {int(p[2:])}, {kind}, {attr}, {adc}, NOT_ON_PWM, NOT_ON_TIMER, {eic[p]}}}, // {index[p]}: {p}\n'
    cpp += '};\nextern "C" unsigned int PINCOUNT_fn() { return PINS_COUNT; }\n'
    cpp += 'const void* g_apTCInstances[TCC_INST_NUM+TC_INST_NUM] = {TCC0,TCC1,TCC2,TC3,TC4,TC5,TC6,TC7};\n'
    cpp += ''.join(f'SERCOM sercom{i}(SERCOM{i});\n' for i in range(6))
    cpp += 'Uart Serial1(&sercom3, PIN_SERIAL1_RX, PIN_SERIAL1_TX, SERCOM_RX_PAD_1, UART_TX_PAD_0);\n'
    cpp += f'Uart Serial2(&sercom1, PIN_SERIAL2_RX, PIN_SERIAL2_TX, SERCOM_RX_PAD_{1 if pins["UART1_RX"] == "PA17" else 2}, UART_TX_PAD_0);\n'
    cpp += 'extern "C" void __attribute__((weak)) SERCOM3_Handler() { Serial1.IrqHandler(); }\n'
    cpp += 'extern "C" void __attribute__((weak)) SERCOM1_Handler() { Serial2.IrqHandler(); }\n'
    cpp += 'void initVariant() { custom_board_early_init(); }\n'
    write(variant / 'variant.cpp', cpp)
    for name in ('custom_board.c', 'custom_board.h'):
        shutil.copy2(firmware / 'src' / name, variant / name)
    shutil.copy2(firmware / 'CustomBootloaderIdentity.h', variant / 'CustomBootloaderIdentity.h')
    shutil.copy2(ROOT / 'app_flash_with_bootloader.ld', variant / 'flash_with_bootloader.ld')
    # Reserve double-tap data plus padding so the initial stack stays 8-byte aligned.
    ld = (variant / 'flash_with_bootloader.ld').read_text()
    ld = ld.replace('LENGTH = 0x00008000\n', 'LENGTH = 0x00008000-8\n')
    if 'LENGTH = 0x00008000-8' not in ld:
        raise ValueError('Review application linker RAM/double-tap reservation.')
    write(variant / 'flash_with_bootloader.ld', ld)
    usb = b['usb']
    flags = f'-D__SAMD21J18A__ -DJ18_REVISED_SPI0={int(spi0_ok)} -DJ18_REVISED_SPI1={int(spi1_ok)} {{build.usb_flags}}'
    if b['crystalless']:
        flags += ' -DCRYSTALLESS'
    properties = {
        'name': 'Custom SAMD21J18 (Native USB)',
        'build.core': 'arduino:arduino', 'build.variant': 'custom_j18',
        'build.board': 'SAMD_CUSTOM_J18', 'build.mcu': 'cortex-m0plus',
        'build.f_cpu': '48000000L', 'build.extra_flags': flags,
        'build.ldscript': 'flash_with_bootloader.ld', 'build.variant_system_lib': '',
        'build.vid': hex(usb['vid']), 'build.pid': hex(usb['app_pid']),
        'build.usb_product': json.dumps(usb['product']), 'build.usb_manufacturer': json.dumps(usb['manufacturer']),
        'upload.tool': 'arduino:bossac', 'upload.tool.default': 'arduino:bossac',
        'upload.protocol': 'sam-ba', 'upload.maximum_size': '253952',
        'upload.maximum_data_size': '32760', 'upload.use_1200bps_touch': 'true',
        'upload.wait_for_upload_port': 'true', 'upload.native_usb': 'true',
        'bootloader.file': 'custom/samd21_sam_ba.bin', 'bootloader.size': '0x2000'
    }
    for i, pid in enumerate((usb['app_pid'], usb['boot_pid'])):
        properties.update({f'vid.{i}': hex(usb['vid']), f'pid.{i}': hex(pid),
                           f'upload_port.{i}.vid': hex(usb['vid']), f'upload_port.{i}.pid': hex(pid)})
    write(dest / 'boards.txt', '# Generated from board.json. Core recipes inherited from arduino:samd.\n' +
          ''.join(f'custom_j18.{k}={v}\n' for k, v in properties.items()))
    boot = dest / 'bootloaders/custom'
    boot.mkdir(parents=True, exist_ok=True)
    for name in ('samd21_sam_ba.bin', 'manifest.json'):
        shutil.copy2(firmware / name, boot / name)
    shutil.copy2(ROOT / 'LICENSE', dest / 'LICENSE')
    write(dest / 'README.md', '# Generated local Custom board profile\n\n'
          'Requires arduino:samd@1.8.14. See the repository bootloader/README.md.\n'
          'Native USB upload uses the inherited bossac recipe. Flash bootloader via the provided OpenOCD configs.\n'
          f'Hardware SPI routes available: SPI0={spi0_ok}, SPI1={spi1_ok}. Physical aliases differ from Zero digital pin numbers.\n'
          'PWM pin metadata is not implemented in this initial variant; use digital GPIO or a reviewed timer driver.\n')
    write(aliases_out or dest / 'PIN_ALIASES.md', '# Generated Custom Arduino pin aliases\n\n'
          'Applies to `custom:samd:custom_j18`, not the Zero profile. Numbers are Arduino table indices.\n\n'
          '| Alias | Physical pin | Arduino index |\n|---|---|---|\n' +
          ''.join(f'| `PIN_{k}` | {v} | {index[v]} |\n' for k, v in pins.items()) + '\n'
          'All package GPIOs also have `J18_PIN_PAxx` / `J18_PIN_PBxx` aliases. USB, SWD, and crystal pins are reserved.\n')
