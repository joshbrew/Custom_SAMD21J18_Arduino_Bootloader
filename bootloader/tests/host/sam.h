// Fake PORT register interface for the boot GPIO sequencing test.
#pragma once
#include <cstdint>
#include <vector>
struct Write { unsigned group, kind, pin; uint32_t value; };
extern std::vector<Write> writes;
enum Kind { PIN_CONFIG, DIR_CLEAR, OUT_SET, OUT_CLEAR, DIR_SET };
struct Register {
  unsigned group=0, kind=0, pin=0;
  Register& operator=(uint32_t value) {
    writes.push_back({group,kind,pin,value}); return *this;
  }
};
struct PinConfig { Register reg; };
struct PortGroup {
  struct { Register reg; } DIRCLR, OUTSET, OUTCLR, DIRSET;
  PinConfig PINCFG[32];
};
struct Port { PortGroup Group[2]; };
extern Port fakePort;
#define PORT (&fakePort)
