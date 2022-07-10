FROM gitpod/workspace-full-vnc

USER gitpod

RUN pyenv install 3.9.13 \
    && pyenv global 3.9.13 \
    && python -m pip install --upgrade pip
