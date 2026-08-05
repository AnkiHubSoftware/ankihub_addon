default:
  just --list

# Set up Python environment and install dependencies (aqt_version: aqt or aqt_legacy)
install aqt_version="aqt":
    uv sync --group dev --group bundle --group bundle_modern --group {{aqt_version}}

lint:
    uv run pre-commit run --all
