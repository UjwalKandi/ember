#pragma once

// Build-time configuration shared by both roles.

#ifndef NODE_ID
#define NODE_ID 1  // override with -DNODE_ID=<1-254>
#endif

#define EMBER_CHANNEL 1          // fixed Wi-Fi channel for ESP-NOW
#define EMBER_DEFAULT_TTL 3      // hop limit (Meshtastic default)
#define EMBER_MAX_BODY 200       // ELP body limit, bytes
#define EMBER_HEARTBEAT_S 30     // default heartbeat interval

#define LED_PIN 21               // XIAO ESP32S3 user LED, active low

// Optional battery sense: XIAO ESP32S3 has no on-board divider, so wire a 2x100k
// divider from BAT+ to a free ADC pin and define BATT_ADC_PIN (e.g. -DBATT_ADC_PIN=A0).
// Without it, nodes report b0.0.
