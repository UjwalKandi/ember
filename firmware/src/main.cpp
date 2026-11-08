#include <Arduino.h>

#include "roles.h"
#include "settings.h"

// One image for every board: the role (arm, node or gateway) is read from flash at boot.
void setup() {
  settings::begin();
  if (settings::role() == settings::ARM) arm::setup();
  else if (settings::role() == settings::GATEWAY) gateway::setup();
  else node::setup();
}

void loop() {
  if (settings::role() == settings::ARM) arm::loop();
  else if (settings::role() == settings::GATEWAY) gateway::loop();
  else node::loop();
}
