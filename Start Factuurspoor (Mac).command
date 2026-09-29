#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo ""
echo "  Factuurspoor wordt gestart. De eerste keer duurt dit een paar minuten..."
echo ""
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "  Benodigd hulpprogramma 'uv' wordt eenmalig geinstalleerd..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv run --python 3.11 python -m app.cli demo
