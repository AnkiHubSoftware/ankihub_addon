#!/bin/bash
set -e

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

function check_uv() {
    uv version
}

function install_uv() {
    # Install uv using the official installer
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Add uv to the current PATH
    export PATH="$HOME/.local/bin:$PATH"
}

function check_llm() {
    llm --version
}

function install_llm() {
    # Install llm using uv
    uv tool install llm

    # Install additional providers
    uv run --no-project llm install -U llm-gemini
    uv run --no-project llm install -U llm-perplexity
    uv run --no-project llm install -U llm-claude-3
}

function get_templates_path() {
    uv run --no-project llm templates path
}

function execute_prompt() {
    # Arguments:
    # $1: template_name
    # $2: note_schema
    # $3: note_content
    uv run --no-project llm -m gpt-4o --no-stream -t "$1" -p note_schema "$2" "$3" -o json_object 1
}

# Main command router
cmd=$1
shift  # Remove first argument (the command) to pass remaining args to functions

case $cmd in
    "check_uv")
        check_uv
        ;;
    "install_uv")
        install_uv
        ;;
    "check_llm")
        check_llm
        ;;
    "install_llm")
        install_llm
        ;;
    "get_templates_path")
        get_templates_path
        ;;
    "execute_prompt")
        execute_prompt "$@"
        ;;
    *)
        echo "Unknown command: $cmd"
        exit 1
        ;;
esac
