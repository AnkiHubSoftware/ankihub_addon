FROM mcr.microsoft.com/devcontainers/python:3.11

RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    dbus-x11 \
    espeak-ng \
    ffmpeg \
    fonts-arphic-ukai \
    fonts-arphic-uming \
    fonts-ipafont-gothic \
    fonts-ipafont-mincho \
    fonts-unfonts-core \
    lame \
    libasound2 \
    libqt5x11extras5 \
    mplayer \
    python3-pyqt5 \
    tigervnc-common \
    tigervnc-standalone-server \
    xfce4 \
    xfce4-goodies \
    xfonts-100dpi \
    xfonts-75dpi \
    xfonts-base \
    xvfb \
    zstd

RUN git clone https://github.com/novnc/noVNC.git /opt/novnc
RUN git clone https://github.com/novnc/websockify /opt/novnc/utils/websockify

COPY ./requirements /requirements
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /requirements/dev.txt

# install anki versions
ARG ANKI_DIR=/opt/anki
RUN mkdir ${ANKI_DIR}
ARG ANKI_2156_FILE=anki-2.1.56-linux-qt6.tar.zst
RUN cd ${ANKI_DIR} && wget -q https://github.com/ankitects/anki/releases/download/2.1.56/${ANKI_2156_FILE} && tar xvaf ${ANKI_2156_FILE} && rm ${ANKI_2156_FILE}
