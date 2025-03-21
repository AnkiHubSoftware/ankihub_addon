#!/bin/bash

# link workspace in anki addons
mkdir -p $HOME/.local/share/Anki2/addons21
ln -s /workspaces/ankihub_addon/ankihub $HOME/.local/share/Anki2/addons21/ankihub

# VNC setup
sudo ln -s $PWD/.devcontainer/config/novnc/index.html /opt/novnc/index.html
cp -rv $PWD/.devcontainer/config/.vnc $HOME/

# Pre-commit setup
pre-commit install

# TODO: Set api key properly
sudo GOOGLE_API_KEY="fake_google_api_key" $HOME/venv/bin/python scripts/build.py
