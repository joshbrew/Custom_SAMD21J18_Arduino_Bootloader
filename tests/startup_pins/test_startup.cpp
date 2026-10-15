#include "Arduino.h"
#include "GeneratedPinMap.h"
#include <assert.h>
#include <stdio.h>
static Port registers;
Port* PORT = &registers;
int main() {
  for (unsigned p = 0; p < 32; ++p) {
    registers.Group[0].PINCFG[p].reg = 0x0b; // Mux + input + sentinel bit.
    registers.Group[1].PINCFG[p].reg = 0x0b;
  }
  // The same all-pin sweep used by Arduino init(), exercising the real core pinMode().
  for (unsigned i = 0; i < NUM_DIGITAL_PINS; ++i) pinMode(i, INPUT);
  for (unsigned i = 0; i < NUM_DIGITAL_PINS; ++i) {
    const auto& p = g_APinDescription[i];
    bool reserved = p.ulPort == PORTA && (p.ulPin == 0 || p.ulPin == 1 || p.ulPin == 30 || p.ulPin == 31);
    assert(registers.Group[p.ulPort].PINCFG[p.ulPin].reg == (reserved ? 0x0b : PORT_PINCFG_INEN));
    if (reserved) {
      pinMode(i, OUTPUT);
      digitalWrite(i, HIGH);
      assert(digitalRead(i) == LOW);
      assert(registers.Group[p.ulPort].PINCFG[p.ulPin].reg == 0x0b);
    }
  }
  puts("PASS: real Arduino pinMode startup sweep preserves SWD/crystal mux and initializes ordinary GPIO.");
}
