// Generated from board.json by build.py; edit board.json instead.
#pragma once
#define J18_BOARD_REVISION "custom-j18-example"
#define J18_BOOT_VERSION "0.1.0"
#define J18_BOOT_INDICATOR 0
#define J18_LED 5u // PA05
#define J18_UART0_TX 22u // PA22
#define J18_UART0_RX 23u // PA23
#define J18_UART1_TX 16u // PA16
#define J18_UART1_RX 17u // PA17
#define J18_SDA 8u // PA08
#define J18_SCL 9u // PA09
#define J18_SPI0_MOSI 44u // PB12
#define J18_SPI0_SCK 45u // PB13
#define J18_SPI0_MISO 43u // PB11
#define J18_CS0 42u // PB10
#define J18_SPI1_MOSI 48u // PB16
#define J18_SPI1_SCK 49u // PB17
#define J18_SPI1_MISO 54u // PB22
#define J18_CS1 46u // PB14
#define J18_SAFE_OUTPUTS(X) 
#define J18_HIGH_IMPEDANCE(X) X(UART0_TX) X(UART0_RX) X(UART1_TX) X(UART1_RX) X(SDA) X(SCL) X(SPI0_MOSI) X(SPI0_SCK) X(SPI0_MISO) X(CS0) X(SPI1_MOSI) X(SPI1_SCK) X(SPI1_MISO) X(CS1)
#ifdef __cplusplus
extern "C" {
#endif
void custom_board_early_init(void);
#ifdef __cplusplus
}
#endif
