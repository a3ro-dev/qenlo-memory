#!/bin/sh
# qenlo-memory: curl -fsSL https://raw.githubusercontent.com/a3ro-dev/qenlo-memory/main/install.sh | sh
set -eu
SRC="qenlo-memory @ https://github.com/a3ro-dev/qenlo-memory/archive/refs/heads/main.zip"

if ! command -v uv >/dev/null 2>&1; then
  echo "  installing uv, the python tool manager qenlo-memory ships through"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  PATH="$HOME/.local/bin:$PATH"
fi
command -v qenlo-memory >/dev/null 2>&1 && qenlo-memory stop >/dev/null 2>&1 || true
uv tool install --force --quiet "$SRC"
"$(uv tool dir --bin)/qenlo-memory" install </dev/tty
