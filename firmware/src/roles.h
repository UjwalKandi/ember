#pragma once

// Role entry points; main.cpp picks one at compile time.
namespace node {
void setup();
void loop();
}

namespace gateway {
void setup();
void loop();
}
