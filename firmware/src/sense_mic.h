#pragma once
#include <Arduino.h>

namespace mic {

struct Reading {
  bool ok;
  uint8_t level;    // 0-100, RMS mapped from -60..-10 dBFS
  uint8_t crackle;  // 0-100, short transients per second (x10)
};

bool begin();
// Blocks for roughly windowMs while reading the PDM microphone.
Reading sample(uint32_t windowMs);

}  // namespace mic
