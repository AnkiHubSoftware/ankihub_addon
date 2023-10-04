FROM mcr.microsoft.com/devcontainers/python:3.11

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

# Setup virtual environment
ENV VIRTUAL_ENV="/home/vscode/venv"
RUN python -m venv $VIRTUAL_ENV
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

# Update shell initialization files for runtime sessions
RUN echo 'export PATH="'${VIRTUAL_ENV}'/bin:$PATH"' >> /home/vscode/.profile

# Install requirements
COPY ./requirements /requirements
RUN pip3 install --upgrade pip && \
    pip3 --no-cache-dir install -r /requirements/dev.txt && \
    rm -rf /tmp/pip-tmp


# Install Anki
ARG ANKI_DIR=/opt/anki
RUN mkdir ${ANKI_DIR}
ARG ANKI_2156_FILE=anki-2.1.56-linux-qt5.tar.zst
RUN cd ${ANKI_DIR} && wget -q https://github.com/ankitects/anki/releases/download/2.1.56/${ANKI_2156_FILE} && tar xvaf ${ANKI_2156_FILE} && rm ${ANKI_2156_FILE}
