FROM gitpod/workspace-full-vnc

USER gitpod

RUN sudo apt-get install -y apt-get install build-essential libgl1-mesa-dev libxkbcommon-x11-0 libpulse-dev libxcb-util1 libxcb-glx0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-render0 libxcb-shape0 libxcb-shm0 libxcb-sync1 libxcb-xfixes0 libxcb-xinerama0 libxcb1

RUN pyenv install 3.9.13 \
    && pyenv global 3.9.13 \
    && python -m pip install --upgrade pip
