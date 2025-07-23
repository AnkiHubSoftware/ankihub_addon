default:
  just --list

# Set up Python environment and install dependencies
install:
    uv sync --dev --group production --group bundle

lint:
    uv run pre-commit run --all
