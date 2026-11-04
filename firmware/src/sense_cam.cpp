#include "sense_cam.h"

#include <esp_camera.h>

namespace cam {
namespace {

// XIAO ESP32S3 Sense camera pin map (Seeed CAMERA_MODEL_XIAO_ESP32S3).
constexpr int PIN_PWDN = -1, PIN_RESET = -1, PIN_XCLK = 10, PIN_SIOD = 40, PIN_SIOC = 39;
constexpr int PIN_Y9 = 48, PIN_Y8 = 11, PIN_Y7 = 12, PIN_Y6 = 14, PIN_Y5 = 16, PIN_Y4 = 18,
              PIN_Y3 = 17, PIN_Y2 = 15, PIN_VSYNC = 38, PIN_HREF = 47, PIN_PCLK = 13;

// Tuning knobs. Flame: % of pixels classed as flame times FLAME_GAIN (≈7% of frame -> 100).
constexpr float FLAME_GAIN = 15.0f;
constexpr float HAZE_GAIN = 2.0f;      // 50% contrast loss -> 100
constexpr float MOTION_GAIN = 2.0f;    // 50-level mean jump -> 100
// Contrast baseline adapts slowly always, and faster if haze has been pinned for a long
// time (a scene change like dusk, not smoke), so it can never latch.
constexpr float BASELINE_ALPHA = 0.002f;
constexpr float BASELINE_ALPHA_FAST = 0.02f;
constexpr uint32_t STUCK_HAZE_MS = 120000;
constexpr int WARMUP_FRAMES = 10;     // auto-exposure settles over the first frames

float contrastBaseline = -1.0f;
float prevMean = -1.0f;
int warmup = 0;
uint32_t hazeSinceMs = 0;             // 0 = haze currently below the SMK threshold

inline bool isFlame(uint8_t r, uint8_t g, uint8_t b) {
  // Bright, red-dominant, warm ordering (R > G > B) with strong red-blue spread.
  return r > 180 && r > g && g > b && (r - b) > 80;
}

uint8_t clamp100(float v) { return v < 0 ? 0 : (v > 100 ? 100 : (uint8_t)v); }

}  // namespace

bool begin() {
  camera_config_t c = {};
  c.pin_pwdn = PIN_PWDN;
  c.pin_reset = PIN_RESET;
  c.pin_xclk = PIN_XCLK;
  c.pin_sccb_sda = PIN_SIOD;
  c.pin_sccb_scl = PIN_SIOC;
  c.pin_d7 = PIN_Y9;
  c.pin_d6 = PIN_Y8;
  c.pin_d5 = PIN_Y7;
  c.pin_d4 = PIN_Y6;
  c.pin_d3 = PIN_Y5;
  c.pin_d2 = PIN_Y4;
  c.pin_d1 = PIN_Y3;
  c.pin_d0 = PIN_Y2;
  c.pin_vsync = PIN_VSYNC;
  c.pin_href = PIN_HREF;
  c.pin_pclk = PIN_PCLK;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_RGB565;
  c.frame_size = FRAMESIZE_QQVGA;  // 160x120 is plenty for colour statistics
  c.jpeg_quality = 12;
  c.fb_count = 2;  // with GRAB_LATEST, two buffers mean each sample is a fresh frame
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  return esp_camera_init(&c) == ESP_OK;
}

Reading sample() {
  Reading out = {false, 0, 0, 0};
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return out;

  uint32_t flamePx = 0, n = 0;
  double sum = 0, sumSq = 0;
  const uint8_t *p = fb->buf;
  // Every 2nd pixel on every 2nd row; RGB565 comes out big-endian from the sensor.
  for (size_t y = 0; y < fb->height; y += 2) {
    for (size_t x = 0; x < fb->width; x += 2) {
      size_t i = (y * fb->width + x) * 2;
      uint16_t px = (p[i] << 8) | p[i + 1];
      uint8_t r = ((px >> 11) & 0x1F) << 3;
      uint8_t g = ((px >> 5) & 0x3F) << 2;
      uint8_t b = (px & 0x1F) << 3;
      float lum = 0.299f * r + 0.587f * g + 0.114f * b;
      sum += lum;
      sumSq += lum * lum;
      if (isFlame(r, g, b)) flamePx++;
      n++;
    }
  }
  esp_camera_fb_return(fb);
  if (n == 0) return out;

  float mean = sum / n;
  float stddev = sqrtf(fmaxf(0.0f, sumSq / n - mean * mean));

  if (warmup < WARMUP_FRAMES) {  // track the scene but report nothing until exposure settles
    warmup++;
    contrastBaseline = stddev;
    prevMean = mean;
    out.ok = true;
    return out;
  }

  out.flame = clamp100(100.0f * flamePx / n * FLAME_GAIN);

  float loss = contrastBaseline > 1.0f ? (contrastBaseline - stddev) / contrastBaseline : 0;
  out.haze = clamp100(loss * 100.0f * HAZE_GAIN);
  uint32_t now = millis();
  if (out.haze >= 25) {
    if (hazeSinceMs == 0) hazeSinceMs = now ? now : 1;
  } else {
    hazeSinceMs = 0;
  }
  bool stuck = hazeSinceMs != 0 && now - hazeSinceMs > STUCK_HAZE_MS;
  contrastBaseline += (stuck ? BASELINE_ALPHA_FAST : BASELINE_ALPHA) * (stddev - contrastBaseline);

  if (prevMean >= 0) out.motion = clamp100(fabsf(mean - prevMean) * MOTION_GAIN);
  prevMean = mean;

  out.ok = true;
  return out;
}

}  // namespace cam
