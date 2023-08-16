FROM nixos/nix

RUN nix-channel --add https://channels.nixos.org/nixos-22.11 nixpkgs && \
    nix-channel --update

WORKDIR /app

ENV DOCKER=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIPENV_CLEAR=1

COPY Pipfile.lock shell.nix ./
RUN nix-shell --run "true"

COPY LICENSE Makefile *.py ./
COPY static ./static/
COPY templates ./templates/

ENV OSM_REVERT_VERSION_SUFFIX=docker-ui
ENTRYPOINT [ "nix-shell", "--run" ]
CMD [ "pipenv run uvicorn main:app --host 0.0.0.0" ]
