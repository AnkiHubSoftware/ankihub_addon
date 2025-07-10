FROM mcr.microsoft.com/devcontainers/python:3.11

# Install UV from the official distroless image with a pinned version
COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /uvx /bin/
ENV UV_SYSTEM_PYTHON 1

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
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
    libxcb-cursor-dev \
    libxkbfile1	\
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    make \
    pkg-config \
    portaudio19-dev \
    rsync \
    zstd \
    xvfb \
    tigervnc-common \
    tigervnc-standalone-server \
    xfce4 \
    xfce4-goodies \
    dbus-x11 \
    xfonts-base \
    xfonts-100dpi \
    xfonts-75dpi \
    qtbase5-dev \
    python3-pyqt5 \
    python3-pyqt5.qtwebengine

RUN git clone https://github.com/novnc/noVNC.git /opt/novnc
RUN git clone https://github.com/novnc/websockify /opt/novnc/utils/websockify

# Update shell initialization files for runtime sessions
RUN echo 'export PATH="'${VIRTUAL_ENV}'/bin:$PATH"' >> /home/vscode/.profile

# Copy dependency files for Docker layer caching.
COPY pyproject.toml uv.lock ./

# Install dependencies using UV
# https://docs.astral.sh/uv/guides/integration/docker/#intermediate-layers
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --dev

# This is copied last to take advantage of Docker layer caching.
COPY . .

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --dev

# Install Anki
ARG ANKI_DIR=/opt/anki
RUN mkdir ${ANKI_DIR}
ARG ANKI_2156_FILE=anki-2.1.56-linux-qt5.tar.zst
RUN cd ${ANKI_DIR} && wget -q https://github.com/ankitects/anki/releases/download/2.1.56/${ANKI_2156_FILE} && tar xvaf ${ANKI_2156_FILE} && rm ${ANKI_2156_FILE}
