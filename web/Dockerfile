FROM python:3.10.8-slim

ENV PYTHONUNBUFFERED=1
ENV OSM_REVERT_VERSION_SUFFIX=docker-ui

RUN apt update && \
    apt install -y curl && \
    curl https://get.docker.com/ | sh && \
    apt clean && \
    pip install \
    --no-cache-dir \
    --disable-pip-version-check \
    pipenv

WORKDIR /app

RUN groupadd --gid 1000 appuser && \
    useradd --gid 1000 --uid 1000 --create-home --no-log-init appuser && \
    usermod --append --groups docker appuser && \
    chown 1000:1000 .

USER 1000:1000

COPY --chown=1000:1000 Pipfile* .
RUN pipenv install --deploy --ignore-pipfile && \
    pipenv --clear

COPY --chown=1000:1000 LICENSE *.py ./
RUN python -m compileall .

ENTRYPOINT ["pipenv", "run", "uvicorn", "main:app"]
CMD ["--host", "0.0.0.0"]
