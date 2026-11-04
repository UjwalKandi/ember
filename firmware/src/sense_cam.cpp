#include "sense_cam.h"

#include <esp_camera.h>

#include "settings.h"

namespace cam {
namespace {

// XIAO ESP32S3 Sense camera pin map (Seeed CAMERA_MODEL_XIAO_ESP32S3).
constexpr int PIN_PWDN = -1, PIN_RESET = -1, PIN_XCLK = 10, PIN_SIOD = 40, PIN_SIOC = 39;
constexpr int PIN_Y9 = 48, PIN_Y8 = 11, PIN_Y7 = 12, PIN_Y6 = 14, PIN_Y5 = 16, PIN_Y4 = 18,
              PIN_Y3 = 17, PIN_Y2 = 15, PIN_VSYNC = 38, PIN_HREF = 47, PIN_PCLK = 13;

// Tuning knobs. Flame: % of pixels classed as flame times flameGain (default 30: ≈3.3% of
// the frame -> 100, i.e. a lighter at 10-15 cm). Runtime-tunable with CAM|gain=N.
float flameGain = 30.0f;
bool swapBytes = true;                 // XIAO OV2640/OV3660 RGB565 arrives little-endian (CAM|swap=0 to flip)
bool inited = false;
constexpr float HAZE_GAIN = 1.2f;      // ~80% contrast loss -> 100 (indoor scenes vary a lot)
constexpr float MOTION_GAIN = 2.0f;    // 50-level mean jump -> 100
// Contrast baseline adapts slowly always, and faster if haze has been pinned for a long
// time (a scene change like dusk, not smoke), so it can never latch.
constexpr float BASELINE_ALPHA = 0.002f;
constexpr float BASELINE_ALPHA_FAST = 0.05f;
constexpr uint32_t STUCK_HAZE_MS = 60000;
constexpr int WARMUP_FRAMES = 10;     // auto-exposure settles over the first frames

float contrastBaseline = -1.0f;
float hazeEma = 0.0f;                 // haze is smoothed: real smoke builds over seconds
constexpr float HAZE_EMA = 0.35f;
float prevMean = -1.0f;
float lastStddev = -1.0f;
int warmup = 0;
uint32_t hazeSinceMs = 0;             // 0 = haze currently below the SMK threshold

// Flame pixels must stand out from the scene (much brighter than the frame average), so warm
// walls, skin and indoor lighting don't count. Two kinds, measured on real XIAO frames:
//  - orange/yellow halo: bright, saturated, R > G > B
//  - clipped core: the sensor saturates the hottest part of a flame to near-white
// A frame only scores if it has an orange halo; white pixels count at most 2x the orange ones,
// so a white flashlight or lamp (no halo) scores 0.
constexpr float FLAME_LUM_MARGIN = 60.0f;
constexpr uint32_t MIN_ORANGE_PX = 3;

inline float luma(uint8_t r, uint8_t g, uint8_t b) { return 0.299f * r + 0.587f * g + 0.114f * b; }
inline bool isOrange(uint8_t r, uint8_t g, uint8_t b) {
  return r >= 200 && (r - g) >= 25 && (g - b) >= 15;
}
inline bool isCore(uint8_t r, uint8_t g, uint8_t b) { return r >= 240 && g >= 235; }

// Scene-relative flame score 0-100 from orange and core pixel counts.
inline uint8_t flameScore(uint32_t orange, uint32_t core, uint32_t n, float gain) {
  if (orange < MIN_ORANGE_PX || n == 0) return 0;
  uint32_t px = orange + min(core, 2 * orange);
  float v = 100.0f * px / n * gain;
  return v > 100 ? 100 : (uint8_t)v;
}

inline void decode(const uint8_t *p, size_t i, bool swap, uint8_t &r, uint8_t &g, uint8_t &b) {
  uint16_t px = swap ? (p[i + 1] << 8) | p[i] : (p[i] << 8) | p[i + 1];
  r = ((px >> 11) & 0x1F) << 3;
  g = ((px >> 5) & 0x3F) << 2;
  b = (px & 0x1F) << 3;
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
  if (inited) return true;
  inited = esp_camera_init(&c) == ESP_OK;
  if (inited) configure(settings::camSwap(), settings::flameGain());
  return inited;
}

void configure(bool swap, float gain) {
  swapBytes = swap;
  flameGain = gain;
}

void diag() {
  if (!inited) {
    Serial.println("LOG|cam not initialised (provision the node first)");
    return;
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("LOG|cam frame grab failed");
    return;
  }
  Serial.printf("LOG|cam frame %ux%u fmt=%d swap=%d gain=%.0f\n", fb->width, fb->height, fb->format,
                swapBytes, flameGain);
  for (int s = 0; s < 2; s++) {
    bool sw = s == 1;
    uint32_t orange = 0, core = 0, n = 0;
    double sr = 0, sg = 0, sb = 0, sl = 0;
    uint8_t br = 0, bg = 0, bb = 0;
    float bestLum = -1;
    for (size_t y = 0; y < fb->height; y += 2) {
      for (size_t x = 0; x < fb->width; x += 2) {
        uint8_t r, g, b;
        decode(fb->buf, (y * fb->width + x) * 2, sw, r, g, b);
        float lum = luma(r, g, b);
        sr += r; sg += g; sb += b; sl += lum; n++;
        if (lum > bestLum) { bestLum = lum; br = r; bg = g; bb = b; }
      }
    }
    float meanLum = sl / n;
    for (size_t y = 0; y < fb->height; y += 2) {
      for (size_t x = 0; x < fb->width; x += 2) {
        uint8_t r, g, b;
        decode(fb->buf, (y * fb->width + x) * 2, sw, r, g, b);
        if (luma(r, g, b) < meanLum + FLAME_LUM_MARGIN) continue;
        if (isOrange(r, g, b)) orange++;
        else if (isCore(r, g, b)) core++;
      }
    }
    Serial.printf("LOG|cam swap=%d mean_rgb=%.0f,%.0f,%.0f brightest_rgb=%u,%u,%u orange_px=%u core_px=%u "
                  "of %u -> flame=%u%s\n", sw, sr / n, sg / n, sb / n, br, bg, bb, orange, core, n,
                  flameScore(orange, core, n, flameGain), sw == swapBytes ? "  <- in use" : "");
  }
  esp_camera_fb_return(fb);
  Serial.println("LOG|cam tip: plain scene -> flame ~0; flame 10-15 cm away -> orange_px > 3 and flame >= 40");
}

Reading sample() {
  Reading out = {false, 0, 0, 0};
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return out;

  uint32_t orange = 0, core = 0, n = 0;
  double sum = 0, sumSq = 0;
  const uint8_t *p = fb->buf;
  // Every 2nd pixel on every 2nd row. Pass 1: scene brightness and contrast.
  for (size_t y = 0; y < fb->height; y += 2) {
    for (size_t x = 0; x < fb->width; x += 2) {
      uint8_t r, g, b;
      decode(p, (y * fb->width + x) * 2, swapBytes, r, g, b);
      float lum = luma(r, g, b);
      sum += lum;
      sumSq += lum * lum;
      n++;
    }
  }
  // Pass 2: flame pixels that stand out from this scene.
  float meanLum = n ? sum / n : 0;
  for (size_t y = 0; y < fb->height; y += 2) {
    for (size_t x = 0; x < fb->width; x += 2) {
      uint8_t r, g, b;
      decode(p, (y * fb->width + x) * 2, swapBytes, r, g, b);
      if (luma(r, g, b) < meanLum + FLAME_LUM_MARGIN) continue;
      if (isOrange(r, g, b)) orange++;
      else if (isCore(r, g, b)) core++;
    }
  }
  esp_camera_fb_return(fb);
  if (n == 0) return out;

  float mean = sum / n;
  float stddev = sqrtf(fmaxf(0.0f, sumSq / n - mean * mean));
  lastStddev = stddev;

  if (warmup < WARMUP_FRAMES) {  // track the scene but report nothing until exposure settles
    warmup++;
    contrastBaseline = stddev;
    prevMean = mean;
    out.ok = true;
    return out;
  }

  out.flame = flameScore(orange, core, n, flameGain);

  if (prevMean >= 0) out.motion = clamp100(fabsf(mean - prevMean) * MOTION_GAIN);
  prevMean = mean;

  // Haze: contrast loss vs the slow baseline, smoothed. While the scene is moving (hands,
  // exposure jumps) contrast is meaningless, so hold the last value instead of spiking.
  float loss = contrastBaseline > 1.0f ? (contrastBaseline - stddev) / contrastBaseline : 0;
  if (out.motion < 40) hazeEma += HAZE_EMA * (clamp100(loss * 100.0f * HAZE_GAIN) - hazeEma);
  out.haze = clamp100(hazeEma);
  uint32_t now = millis();
  if (out.haze >= 25) {
    if (hazeSinceMs == 0) hazeSinceMs = now ? now : 1;
  } else {
    hazeSinceMs = 0;
  }
  bool stuck = hazeSinceMs != 0 && now - hazeSinceMs > STUCK_HAZE_MS;
  if (out.motion < 40)
    contrastBaseline += (stuck ? BASELINE_ALPHA_FAST : BASELINE_ALPHA) * (stddev - contrastBaseline);

  out.ok = true;
  return out;
}

void rebase() {
  if (lastStddev >= 0) contrastBaseline = lastStddev;
  hazeSinceMs = 0;
  hazeEma = 0;
}

}  // namespace cam
