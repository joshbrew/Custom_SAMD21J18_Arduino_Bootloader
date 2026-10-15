#pragma once
#include <stdint.h>
typedef uint32_t pin_size_t;
enum PinMode { INPUT, INPUT_PULLUP, INPUT_PULLDOWN, OUTPUT };
enum PinStatus { LOW, HIGH };
enum EPioType { PIO_NOT_A_PIN = -1, PIO_DIGITAL, PIO_SERCOM, PIO_SERCOM_ALT, PIO_COM };
enum EPortType { PORTA, PORTB };
struct PinDescription { EPortType ulPort; uint32_t ulPin; EPioType ulPinType; };
union PinConfig {
  uint8_t reg;
  struct { uint8_t PMUXEN:1, INEN:1, PULLEN:1, reserved:5; } bit;
};
struct Register32 { uint32_t reg; };
struct PortGroup { PinConfig PINCFG[32]; Register32 DIRCLR, DIRSET, OUTSET, OUTCLR, IN; };
struct Port { PortGroup Group[2]; };
extern Port* PORT;
extern const PinDescription g_APinDescription[];
#define PORT_PINCFG_INEN 2u
#define PORT_PINCFG_PULLEN 4u
extern "C" {
void pinMode(pin_size_t, PinMode);
void digitalWrite(pin_size_t, PinStatus);
PinStatus digitalRead(pin_size_t);
}
