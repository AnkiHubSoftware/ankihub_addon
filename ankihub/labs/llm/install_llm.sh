#!/bin/bash
set -e

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

# Install base llm package if not already installed
if ! command -v llm &> /dev/null; then
    uv tool install llm
fi

# Install additional providers
providers=("llm-gemini" "llm-perplexity" "llm-claude-3")
for provider in "${providers[@]}"; do
    uv run --no-project llm install -U "$provider"
done
