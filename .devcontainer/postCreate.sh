#!/bin/bash

# link workspace in anki addons
mkdir -p $HOME/.local/share/Anki2/addons21
ln -s /workspaces/ankihub_addon/ankihub $HOME/.local/share/Anki2/addons21/ankihub

# VNC setup
sudo ln -s $PWD/.devcontainer/config/novnc/index.html /opt/novnc/index.html
cp -rv $PWD/.devcontainer/config/.vnc $HOME/

# Install Python dependencies
VENV_PATH="/home/vscode/venv"
python -m venv ${VENV_PATH}
${VENV_PATH}/bin/pip install --upgrade pip
${VENV_PATH}/bin/pip --no-cache-dir install -r /workspaces/ankihub_addon/requirements/dev.txt
