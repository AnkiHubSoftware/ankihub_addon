#!/bin/bash

# link workspace in anki addons
mkdir -p $HOME/.local/share/Anki2/addons21
ln -s /workspaces/ankihub $HOME/.local/share/Anki2/addons21/ankihub

# VNC setup
sudo ln -s $PWD/.devcontainer/config/novnc/index.html /opt/novnc/index.html
cp -rv $PWD/.devcontainer/config/.vnc $HOME/