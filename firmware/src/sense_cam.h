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
// Adopt the current scene contrast as the haze baseline (gateway `SET|rebase1`), so the
// present ambient haze (fog, dust) reads ~0 from now on.
void rebase();
// Runtime calibration (persisted by settings, set over USB: CAM|swap=1, CAM|gain=40).
void configure(bool swapBytes, float flameGain);
// Print LOG|cam diagnostics for one frame (colour stats, flame pixels, both byte orders).
void diag();

}  // namespace cam
