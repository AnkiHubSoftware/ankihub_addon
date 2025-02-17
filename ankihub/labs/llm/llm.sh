#!/bin/bash
set -e

# Ensure ~/.local/bin is in PATH
export PATH="$HOME/.local/bin:$PATH"

# Define UV executable path
UV_PATH="$HOME/.local/bin/uv"

function check_uv() {
    "$UV_PATH" version
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
    "$UV_PATH" tool install llm
}

function install_providers(){
    # Install additional providers
    "$UV_PATH" run --no-project llm install -U llm-gemini
    "$UV_PATH" run --no-project llm install -U llm-perplexity
    "$UV_PATH" run --no-project llm install -U llm-claude-3
}

function get_templates_path() {
    "$UV_PATH" run --no-project llm templates path
}

function get_keys_path() {
    "$UV_PATH" run --no-project llm keys path
}

function execute_prompt() {
    # Arguments:
    # $1: template_name
    # $2: note_schema
    # $3: note_content
    "$UV_PATH" run --no-project llm -m gpt-4o --no-stream -t "$1" "$2"
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
    "install_providers")
        install_providers
        ;;
    "get_templates_path")
        get_templates_path
        ;;
    "get_keys_path")
        get_keys_path
        ;;
    "execute_prompt")
        execute_prompt "$@"
        ;;
    *)
        echo "Unknown command: $cmd"
        exit 1
        ;;
esac
