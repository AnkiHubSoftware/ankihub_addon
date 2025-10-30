default:
  just --list

# Set up Python environment and install dependencies (aqt_version: aqt, aqt_25_2_7, or aqt_legacy)
install aqt_version="aqt":
    uv sync --group dev --group bundle --group {{aqt_version}}

lint:
    uv run pre-commit run --all
