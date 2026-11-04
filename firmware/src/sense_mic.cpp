#include "sense_mic.h"

#include <ESP_I2S.h>

namespace mic {
namespace {

constexpr int PIN_CLK = 42, PIN_DATA = 41;
constexpr uint32_t RATE = 16000;
constexpr int BLOCK = 80;                  // 5 ms blocks for transient detection
constexpr float TRANSIENT_RATIO = 4.0f;    // block peak vs running block average
constexpr float TRANSIENT_FLOOR = 2000.0f; // absolute peak floor (int16 units)

I2SClass i2s;
float dc = 0;
float blockAvg = 500.0f;

uint8_t clamp100(float v) { return v < 0 ? 0 : (v > 100 ? 100 : (uint8_t)v); }

}  // namespace

bool begin() {
  i2s.setPinsPdmRx(PIN_CLK, PIN_DATA);
  return i2s.begin(I2S_MODE_PDM_RX, RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
}

Reading sample(uint32_t windowMs) {
  Reading out = {false, 0, 0};
  int16_t buf[BLOCK];
  uint32_t blocks = (RATE / BLOCK) * windowMs / 1000;
  double sumSq = 0;
  uint32_t n = 0, transients = 0, refractory = 0;

  for (uint32_t b = 0; b < blocks; b++) {
    size_t got = i2s.readBytes((char *)buf, sizeof(buf)) / sizeof(int16_t);
    if (got == 0) continue;
    float peak = 0;
    for (size_t i = 0; i < got; i++) {
      dc += 0.001f * (buf[i] - dc);  // slow DC removal
      float s = buf[i] - dc;
      sumSq += s * s;
      peak = fmaxf(peak, fabsf(s));
    }
    n += got;
    // A crackle is a short spike well above the recent envelope; 2-block refractory.
    if (refractory > 0) {
      refractory--;
    } else if (peak > TRANSIENT_FLOOR && peak > TRANSIENT_RATIO * blockAvg) {
      transients++;
      refractory = 2;
    }
    blockAvg += 0.05f * (peak - blockAvg);
  }
  if (n == 0) return out;

  float rms = sqrtf(sumSq / n);
  float dbfs = 20.0f * log10f(fmaxf(rms, 1.0f) / 32768.0f);
  out.level = clamp100((dbfs + 60.0f) * 2.0f);
  // Fire crackle is a burst of pops: one or two clicks (a twig, a door) stay below the
  // CRK threshold of 40; 3 transients in the window reach it, each extra adds 15.
  out.crackle = transients >= 3 ? clamp100(40.0f + (transients - 3) * 15.0f) : (uint8_t)(transients * 12);
  out.ok = true;
  return out;
}

}  // namespace mic
