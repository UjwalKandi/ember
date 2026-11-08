#!/usr/bin/env bash
# Remove leftovers EMBER no longer needs (old Python 3.9 user installs, caches, regenerable data).
set -uo pipefail
echo "== old macOS Python 3.9 user packages (replaced by the project .venv)"
/usr/bin/python3 -m pip uninstall -y modal platformio pyserial grpclib synchronicity 2>/dev/null
/usr/bin/python3 -m pip cache purge 2>/dev/null
cd "$(dirname "$0")/.."
echo "== regenerable data/results (recreate with python -m training.gen_data)"
rm -rf data firmware/.pio
du -sh ~/Library/Python/3.9 2>/dev/null
echo "done"
