#!/usr/bin/env bash
# Remove everything EMBER created on Modal so nothing can bill you, then remove the client.
# Run from the repo with the venv active:  scripts/cleanup_modal.sh
set -uo pipefail
M="python -m modal"
echo "== apps";    $M app list
for id in $($M app list --json 2>/dev/null | python -c "import json,sys;[print(a['App ID']) for a in json.load(sys.stdin) if a.get('State') not in ('stopped',)]" 2>/dev/null); do
  echo "stopping $id"; $M app stop "$id"
done
echo "== volumes"; $M volume list
for v in ember-hf-cache ember-runs; do $M volume delete "$v" --yes 2>/dev/null && echo "deleted volume $v" || echo "no volume $v"; done
echo "== secrets"; $M secret list
for s in huggingface ember-llm; do $M secret delete "$s" --yes 2>/dev/null && echo "deleted secret $s" || echo "no secret $s"; done
echo "== after cleanup"; $M app list; $M volume list
python -m pip uninstall -y modal
rm -f ~/.modal.toml && echo "removed ~/.modal.toml (Modal login)"
echo
echo "Next: revoke the Hugging Face token that was stored in Modal (huggingface.co -> Settings -> Access Tokens)."
