#!/bin/bash
set -e

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

# Arguments:
# $1: template_name
# $2: note_schema
# $3: note_content

uv run --no-project llm \
    -m gpt-4o \
    --no-stream \
    -t "$1" \
    -p note_schema "$2" \
    "$3" \
    -o json_object 1
