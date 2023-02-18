ARG NIX_PROFILE=/build-profile
ARG NIX_TRANSFER_DIR=/fake-nix-store

FROM nixos/nix AS nix

ARG NIX_PROFILE
ARG NIX_TRANSFER_DIR

COPY shell.nix ./

RUN nix-channel --add https://channels.nixos.org/nixos-22.11 nixpkgs && \
    nix-channel --update && \
    mkdir --parents $NIX_TRANSFER_DIR && \
    nix-env --profile $NIX_PROFILE --install --attr buildInputs --file shell.nix && \
    cp --archive $(nix-store --query --requisites $NIX_PROFILE) $NIX_TRANSFER_DIR

FROM alpine

ARG NIX_PROFILE
ARG NIX_TRANSFER_DIR

COPY --from=nix $NIX_TRANSFER_DIR /nix/store
COPY --from=nix $NIX_PROFILE /usr/local

WORKDIR /app

RUN addgroup -g 1000 appuser && \
    adduser -u 1000 -G appuser -D appuser && \
    chown 1000:1000 .

USER 1000:1000

ENV PIPENV_VENV_IN_PROJECT=1

COPY --chown=1000:1000 Pipfile* ./

RUN pipenv install --deploy --ignore-pipfile --keep-outdated && \
    pipenv --clear

ENV PATH="/app/.venv/bin:$PATH"

COPY --chown=1000:1000 LICENSE *.py ./

RUN python -m compileall .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV OSM_REVERT_VERSION_SUFFIX=docker

ENTRYPOINT ["python", "main.py"]
