#pragma once
#include <Arduino.h>

namespace cam {

struct Reading {
  bool ok;
  uint8_t flame;   // 0-100, share of bright red/orange pixels
  uint8_t haze;    // 0-100, contrast loss vs slow baseline
  uint8_t motion;  // 0-100, frame-to-frame brightness jump
};

bool begin();
Reading sample();

}  // namespace cam
