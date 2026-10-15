/* Custom application vector checks; LGPL-2.1-or-later. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

static inline bool custom_app_vectors_valid(uint32_t sp, uint32_t reset)
{
  /* AAPCS requires an 8-byte aligned stack. The top of RAM is a valid MSP.
   * Applications must enter Thumb code outside the protected 8 KB boot region.
   */
  return sp > 0x20000000ul && sp <= 0x20008000ul && !(sp & 7ul)
      && (reset & 1ul) && (reset & ~1ul) >= 0x2000ul
      && (reset & ~1ul) < 0x40000ul;
}
