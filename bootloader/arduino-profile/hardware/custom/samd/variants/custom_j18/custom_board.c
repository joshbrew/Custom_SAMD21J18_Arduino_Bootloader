/* Custom adaptation, LGPL-2.1-or-later; see ../LICENSE.
 * Runs before the application decision and uses no peripheral clocks or delays.
 * Writes the output latch BEFORE enabling each output to avoid an active pulse.
 */
#include <sam.h>
#include "custom_board.h"

static inline void output(unsigned pin, unsigned high)
{
  PortGroup *p = &PORT->Group[pin / 32];
  unsigned bit = pin % 32;
  p->PINCFG[bit].reg = 0;
  if (high) p->OUTSET.reg = 1ul << bit;
  else p->OUTCLR.reg = 1ul << bit;
  p->DIRSET.reg = 1ul << bit;
}

static void high_impedance(unsigned pin)
{
  PortGroup *p = &PORT->Group[pin / 32];
  unsigned bit = pin % 32;
  p->DIRCLR.reg = 1ul << bit;
  p->PINCFG[bit].reg = 0; /* No mux, pull-up/down or input buffer. */
}

void custom_board_early_init(void)
{
#define J18_OUTPUT(name, level) output(J18_##name, level);
  J18_SAFE_OUTPUTS(J18_OUTPUT)
#undef J18_OUTPUT
#define J18_INPUT(name) high_impedance(J18_##name);
  J18_HIGH_IMPEDANCE(J18_INPUT)
#undef J18_INPUT
#if J18_BOOT_INDICATOR
  output(J18_LED, 0);
#endif
}
