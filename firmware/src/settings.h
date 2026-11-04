#pragma once
#include <Arduino.h>

// Runtime configuration stored in flash (NVS), so every board runs the same firmware image
// and is provisioned over USB serial: `CFG|id=3`, `CFG|role=gateway`. See protocol/SPEC.md §3.
namespace settings {

enum Role : uint8_t { NODE = 0, GATEWAY = 1 };

void begin();
Role role();
uint8_t nodeId();          // 0 = unprovisioned (nodes stay idle until given an id)
const char *roleName();
bool camSwap();            // RGB565 byte order (CAM|swap=1)
float flameGain();         // flame sensitivity (CAM|gain=N, default 30)
uint8_t flickPct();        // flicker needed to count as flame, % (CAM|flick=N, default 15, 0 = off)

// Persist and reboot.
void setRole(Role r);
void setId(uint8_t id);
void setCam(bool swap, float gain, uint8_t flick);   // persists and applies immediately

}  // namespace settings

// Shared USB serial console: line reader plus the commands every role understands
// (CFG?, CFG|id=N, CFG|role=node|gateway, TEST, REBOOT, CAM?, CAM|swap=0/1, CAM|gain=N,
// CAM|flick=N).
// Role-specific lines (TX|...) are handed back to the caller.
namespace console {

using TestFn = void (*)();

void begin(TestFn selftest);
// Returns true and fills `out` when a complete line arrived that the role must handle.
bool poll(char *out, size_t n);

}  // namespace console
