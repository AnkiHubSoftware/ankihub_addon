# Ensure ~/.local/bin is in PATH
$env:PATH = "$HOME\.local\bin;$env:PATH"

# Define UV executable path
$UV_PATH = "$HOME\.local\bin\uv"

function Check-Uv {
    & "$UV_PATH" version
}

function Install-Uv {
    # Install uv using the official installer
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression

    # Add uv to the current PATH
    $env:PATH = "$HOME\.local\bin;$env:PATH"
}

function Check-Llm {
    & llm --version
}

function Install-Llm {
    # Install llm using uv
    & "$UV_PATH" tool install llm
}

function Install-Provider {
    param (
        [string]$provider
    )
    Write-Output "Installing provider: '$provider'"
    & "$UV_PATH" run --no-project llm install -U $provider
}

function Get-Templates-Path {
    & "$UV_PATH" run --no-project llm templates path
}

function Get-Keys-Path {
    & "$UV_PATH" run --no-project llm keys path
}

function Execute-Prompt {
    param (
        [string]$templateName,
        [string]$noteContent
    )
    & "$UV_PATH" run --no-project llm -m gpt-4o --no-stream -t "$templateName" "$noteContent"
}

$cmd = $args[0]
$args = $args[1..$args.Length]

switch ($cmd) {
    "check_uv" { Check-Uv }
    "install_uv" { Install-Uv }
    "check_llm" { Check-Llm }
    "install_llm" { Install-Llm }
    "install_provider" { Install-Provider -provider $args[0] }
    "get_templates_path" { Get-Templates-Path }
    "get_keys_path" { Get-Keys-Path }
    "execute_prompt" { Execute-Prompt -templateName $args[0] -noteContent $args[1] }
    default {
        Write-Output "Unknown command: $cmd"
        exit 1
    }
}
