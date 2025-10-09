default:
  just --list

# Set up Python environment and install dependencies
install:
    uv sync --group bundle --group aqt

lint:
    uv run pre-commit run --all
