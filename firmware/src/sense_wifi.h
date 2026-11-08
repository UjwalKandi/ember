#pragma once
#include <Arduino.h>

#include "features.h"

// Passive Wi-Fi capture: promiscuous mode with the management-frame filter only (data frames
// never reach us), channel hopping over a dwell plan, one feature window per dwell.
// Receive only: this module never transmits.
namespace wifi {

constexpr uint8_t MAX_PLAN = 14;

bool begin();
// Channels 1-13 in visiting order and the dwell per channel.
void setPlan(const uint8_t *channels, uint8_t n, uint16_t dwellMs);
void printPlan();  // LOG|plan ...

// Call often. Drains captured frames into the extractor; when the current dwell has ended,
// fills `out`, hops to the next channel and returns true.
bool poll(features::Window &out);

features::Extractor &extractor();
uint16_t fps(uint8_t ch);  // frames/s on `ch` during its last dwell
uint32_t drops();          // frames parsed but not queued (counts stay exact; features may undercount)
uint32_t captured();       // management frames captured since boot

}  // namespace wifi
