#!/bin/bash
set -e

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

# Check if uv is already installed
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
