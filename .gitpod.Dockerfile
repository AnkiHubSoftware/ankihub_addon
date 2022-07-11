FROM gitpod/workspace-full-vnc

USER gitpod

RUN sudo apt-get install -y \
    findutils \
    g++ \
    gcc \
    git \
    grep \
    libdbus-1-3 \
    libegl1 \
    libfontconfig1 \
    libgl1 \
    libgstreamer-gl1.0-0 \
    libgstreamer-plugins-base1.0 \
    libgstreamer1.0-0 \
    libnss3 \
    libpulse-mainloop-glib0 \
    libpulse-mainloop-glib0 \
    libssl-dev \
    libxcomposite1 \
    libxcursor1 \
    libxi6 \
    libxkbcommon-x11-0 \
    libxkbcommon0 \
    libxkbfile1	\
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    make \
    pkg-config \
    portaudio19-dev \
    rsync \
    zstd \
    python3-pyqt5

RUN pyenv install 3.9.13
RUN pyenv global 3.9.13

COPY --chown=gitpod ./requirements /requirements
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /requirements/dev.txt
