#include "sense_cam.h"

#include <esp_camera.h>

#include "settings.h"

namespace cam {
namespace {

// XIAO ESP32S3 Sense camera pin map (Seeed CAMERA_MODEL_XIAO_ESP32S3).
constexpr int PIN_PWDN = -1, PIN_RESET = -1, PIN_XCLK = 10, PIN_SIOD = 40, PIN_SIOC = 39;
constexpr int PIN_Y9 = 48, PIN_Y8 = 11, PIN_Y7 = 12, PIN_Y6 = 14, PIN_Y5 = 16, PIN_Y4 = 18,
              PIN_Y3 = 17, PIN_Y2 = 15, PIN_VSYNC = 38, PIN_HREF = 47, PIN_PCLK = 13;

// Runtime calibration (CAM|gain=N, CAM|swap=0/1, CAM|flick=N), loaded from flash in begin().
float flameGain = 30.0f;               // % of frame that is flame x gain (30: ~3.3% -> 100)
bool swapBytes = true;                 // XIAO OV2640/OV3660 RGB565 arrives little-endian
uint8_t flickPct = 15;                 // min frame-to-frame change of the flame area, % (0 = off)
bool inited = false;

constexpr float HAZE_GAIN = 1.2f;      // ~80% contrast loss -> 100 (indoor scenes vary a lot)
constexpr float MOTION_GAIN = 2.0f;    // 50-level mean jump -> 100
// Contrast baseline adapts slowly always, and faster if haze has been pinned for a long
// time (a scene change like dusk, not smoke), so it can never latch.
constexpr float BASELINE_ALPHA = 0.002f;
constexpr float BASELINE_ALPHA_FAST = 0.05f;
constexpr uint32_t STUCK_HAZE_MS = 60000;
constexpr int WARMUP_FRAMES = 10;      // auto-exposure settles over the first frames
constexpr float HAZE_EMA = 0.35f;      // haze is smoothed: real smoke builds over seconds

// Flame pixels must stand out from the scene (much brighter than the frame average), so warm
// walls, skin and indoor lighting don't count. Two kinds, measured on real XIAO frames:
//  - orange/yellow halo: bright, saturated, R > G > B
//  - clipped core: the sensor saturates the hottest part of a flame to near-white
// Colour alone can't tell a flame from a lamp or flashlight (same clipped core + warm fringe),
// so each sample looks at several quick frames: a flame's bright area flickers frame to frame,
// a lamp's doesn't.
constexpr float FLAME_LUM_MARGIN = 60.0f;
constexpr uint32_t MIN_ORANGE_PX = 3;
constexpr int BURST_FRAMES = 4;

float contrastBaseline = -1.0f;
float hazeEma = 0.0f;
float prevMean = -1.0f;
float lastStddev = -1.0f;
int warmup = 0;
uint32_t hazeSinceMs = 0;              // 0 = haze currently below the SMK threshold

struct FrameStats {
  uint32_t n = 0, orange = 0, core = 0;
  float mean = 0, stddev = 0;
  double sr = 0, sg = 0, sb = 0;
  uint8_t br = 0, bg = 0, bb = 0;      // brightest pixel
};

inline float luma(uint8_t r, uint8_t g, uint8_t b) { return 0.299f * r + 0.587f * g + 0.114f * b; }
inline bool isOrange(uint8_t r, uint8_t g, uint8_t b) { return r >= 200 && (r - g) >= 25 && (g - b) >= 15; }
inline bool isCore(uint8_t r, uint8_t g, uint8_t b) { return r >= 240 && g >= 235; }

inline void decode(const uint8_t *p, size_t i, bool swap, uint8_t &r, uint8_t &g, uint8_t &b) {
  uint16_t px = swap ? (p[i + 1] << 8) | p[i] : (p[i] << 8) | p[i + 1];
  r = ((px >> 11) & 0x1F) << 3;
  g = ((px >> 5) & 0x3F) << 2;
  b = (px & 0x1F) << 3;
}

uint8_t clamp100(float v) { return v < 0 ? 0 : (v > 100 ? 100 : (uint8_t)v); }

// Flame-pixel weight of a frame: orange halo plus clipped core (core capped at 2x the halo).
inline uint32_t flamePx(const FrameStats &f) {
  return f.orange < MIN_ORANGE_PX ? 0 : f.orange + min(f.core, 2 * f.orange);
}

// Every 2nd pixel on every 2nd row. Pass 1: brightness and contrast. Pass 2: flame pixels
// that stand out from this scene.
FrameStats analyse(const camera_fb_t *fb, bool swap) {
  FrameStats f;
  double sl = 0, sq = 0;
  float best = -1;
  for (size_t y = 0; y < fb->height; y += 2) {
    for (size_t x = 0; x < fb->width; x += 2) {
      uint8_t r, g, b;
      decode(fb->buf, (y * fb->width + x) * 2, swap, r, g, b);
      float l = luma(r, g, b);
      sl += l;
      sq += l * l;
      f.sr += r; f.sg += g; f.sb += b;
      if (l > best) { best = l; f.br = r; f.bg = g; f.bb = b; }
      f.n++;
    }
  }
  if (!f.n) return f;
  f.mean = sl / f.n;
  f.stddev = sqrtf(fmaxf(0.0f, sq / f.n - f.mean * f.mean));
  for (size_t y = 0; y < fb->height; y += 2) {
    for (size_t x = 0; x < fb->width; x += 2) {
      uint8_t r, g, b;
      decode(fb->buf, (y * fb->width + x) * 2, swap, r, g, b);
      if (luma(r, g, b) < f.mean + FLAME_LUM_MARGIN) continue;
      if (isOrange(r, g, b)) f.orange++;
      else if (isCore(r, g, b)) f.core++;
    }
  }
  return f;
}

struct Burst {
  FrameStats last;
  uint32_t px[BURST_FRAMES] = {};
  int frames = 0;
  float flicker = 0;      // (max - min) / mean of the flame area across the burst, %
  uint8_t score = 0;      // 0-100 after the flicker test
  bool steady = false;    // flame-coloured but static: a lamp, flashlight or glare
};

Burst burst(int frames, bool swap) {
  Burst b;
  uint32_t sum = 0, lo = UINT32_MAX, hi = 0;
  for (int i = 0; i < frames; i++) {
    camera_fb_t *fb = esp_camera_fb_get();   // GRAB_LATEST + 2 buffers: each call is a new frame
    if (!fb) break;
    b.last = analyse(fb, swap);
    esp_camera_fb_return(fb);
    uint32_t px = flamePx(b.last);
    b.px[b.frames++] = px;
    sum += px;
    lo = min(lo, px);
    hi = max(hi, px);
  }
  if (!b.frames || !b.last.n || sum == 0) return b;
  float mean = (float)sum / b.frames;
  b.flicker = 100.0f * (hi - lo) / mean;
  b.score = clamp100(100.0f * mean / b.last.n * flameGain);
  if (flickPct && b.frames > 1 && b.flicker < flickPct) {
    b.steady = true;
    b.score = 0;
  }
  return b;
}

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
  c.fb_count = 2;  // with GRAB_LATEST, two buffers mean each grab is a fresh frame
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (inited) return true;
  inited = esp_camera_init(&c) == ESP_OK;
  if (inited) configure(settings::camSwap(), settings::flameGain(), settings::flickPct());
  return inited;
}

void configure(bool swap, float gain, uint8_t flick) {
  swapBytes = swap;
  flameGain = gain;
  flickPct = flick;
}

void diag() {
  if (!inited) {
    Serial.println("LOG|cam not initialised (provision the node first)");
    return;
  }
  Serial.printf("LOG|cam swap=%d gain=%.0f flick=%u%%\n", swapBytes, flameGain, flickPct);
  for (int s = 0; s < 2; s++) {
    bool sw = s == 1;
    Burst b = burst(6, sw);
    const FrameStats &f = b.last;
    if (!f.n) {
      Serial.println("LOG|cam frame grab failed");
      return;
    }
    char px[64];
    int k = 0;
    for (int i = 0; i < b.frames; i++) k += snprintf(px + k, sizeof(px) - k, "%s%u", i ? "," : "", (unsigned)b.px[i]);
    Serial.printf("LOG|cam swap=%d mean_rgb=%.0f,%.0f,%.0f brightest_rgb=%u,%u,%u orange_px=%u core_px=%u "
                  "flame_px/frame=%s flicker=%.0f%% -> flame=%u%s%s\n",
                  sw, f.sr / f.n, f.sg / f.n, f.sb / f.n, f.br, f.bg, f.bb, f.orange, f.core, px, b.flicker,
                  b.score, b.steady ? " (steady light: suppressed)" : "", sw == swapBytes ? "  <- in use" : "");
  }
  Serial.println("LOG|cam tip: nothing -> flame 0; lamp/flashlight -> steady light: suppressed; "
                 "flame 10-15 cm -> flicker above the flick threshold and flame >= 40");
}

Reading sample() {
  Reading out = {false, 0, 0, 0};
  Burst b = burst(BURST_FRAMES, swapBytes);
  const FrameStats &f = b.last;
  if (!f.n) return out;
  lastStddev = f.stddev;

  if (warmup < WARMUP_FRAMES) {  // track the scene but report nothing until exposure settles
    warmup++;
    contrastBaseline = f.stddev;
    prevMean = f.mean;
    out.ok = true;
    return out;
  }

  out.flame = b.score;

  if (prevMean >= 0) out.motion = clamp100(fabsf(f.mean - prevMean) * MOTION_GAIN);
  prevMean = f.mean;

  // Haze: contrast loss vs the slow baseline, smoothed. While the scene is moving (hands,
  // exposure jumps) contrast is meaningless, so hold the last value instead of spiking.
  float loss = contrastBaseline > 1.0f ? (contrastBaseline - f.stddev) / contrastBaseline : 0;
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
    contrastBaseline += (stuck ? BASELINE_ALPHA_FAST : BASELINE_ALPHA) * (f.stddev - contrastBaseline);

  out.ok = true;
  return out;
}

void rebase() {
  if (lastStddev >= 0) contrastBaseline = lastStddev;
  hazeSinceMs = 0;
  hazeEma = 0;
}

}  // namespace cam
