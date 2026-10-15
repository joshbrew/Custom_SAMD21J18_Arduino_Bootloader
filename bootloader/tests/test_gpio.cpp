// Exercise the actual boot GPIO implementation against logged registers.
#include <cassert>
#include <cstdio>
#include "host/sam.h"
#include "../src/custom_board.h"
#include "../src/custom_vectors.h"
std::vector<Write> writes;
Port fakePort;
// Exercise both driven states and high impedance with a reviewed test fixture.
#undef J18_SAFE_OUTPUTS
#define J18_SAFE_OUTPUTS(X) X(CS0, 1) X(CS1, 0)
#undef J18_HIGH_IMPEDANCE
#define J18_HIGH_IMPEDANCE(X) X(UART0_TX) X(UART0_RX)
#include "../src/custom_board.c"

int main() {
  assert(custom_app_vectors_valid(0x20008000,0x2001));
  assert(custom_app_vectors_valid(0x20007ff8,0x3ffff));
  assert(!custom_app_vectors_valid(0xffffffff,0xffffffff));
  assert(!custom_app_vectors_valid(0x20007ffc,0x2001));
  assert(!custom_app_vectors_valid(0x20008000,0x2000));
  assert(!custom_app_vectors_valid(0x20008000,0x1001));
  assert(!custom_app_vectors_valid(0x20008000,0x40001));
  assert(!custom_app_vectors_valid(0x20000000,0x2001));
  for (unsigned group=0;group<2;++group) {
    auto& p=fakePort.Group[group];
    p.DIRCLR.reg={group,DIR_CLEAR,0}; p.OUTSET.reg={group,OUT_SET,0};
    p.OUTCLR.reg={group,OUT_CLEAR,0}; p.DIRSET.reg={group,DIR_SET,0};
    for (unsigned pin=0;pin<32;++pin) p.PINCFG[pin].reg={group,PIN_CONFIG,pin};
  }
  custom_board_early_init();
  size_t i=0;
  auto output=[&](unsigned pin,unsigned high) {
    const unsigned g=pin/32, bit=pin%32;
    assert(i+3<=writes.size());
    const auto a=writes[i++],b=writes[i++],c=writes[i++];
    assert(a.group==g && a.kind==PIN_CONFIG && a.pin==bit && a.value==0);
    assert(b.group==g && b.kind==static_cast<unsigned>(high?OUT_SET:OUT_CLEAR) && b.value==(1ul<<bit));
    assert(c.group==g && c.kind==DIR_SET && c.value==(1ul<<bit));
  };
#define EXPECT_OUTPUT(name, level) output(J18_##name, level);
  J18_SAFE_OUTPUTS(EXPECT_OUTPUT)
#undef EXPECT_OUTPUT
  auto input=[&](unsigned pin) {
    const unsigned g=pin/32, bit=pin%32;
    assert(i+2<=writes.size());
    const auto a=writes[i++],b=writes[i++];
    assert(a.group==g && a.kind==DIR_CLEAR && a.value==(1ul<<bit));
    assert(b.group==g && b.kind==PIN_CONFIG && b.pin==bit && b.value==0);
  };
#define EXPECT_INPUT(name) input(J18_##name);
  J18_HIGH_IMPEDANCE(EXPECT_INPUT)
#undef EXPECT_INPUT
#if J18_BOOT_INDICATOR
  output(J18_LED,0);
#endif
  assert(i==writes.size()); // No undeclared or reserved pin was touched.
  for (const auto &w:writes) {
    if (w.kind==DIR_SET || w.kind==DIR_CLEAR) {
      if(w.group==0) assert(!(w.value & ((1ul<<0)|(1ul<<1)|(1ul<<24)|(1ul<<25)|(1ul<<30)|(1ul<<31))));
    }
  }
  std::puts("PASS: application vectors, latch-before-output ordering, input states and reserved pins.");
}
