# Sentinel service image (#126). Runs either the web process (webhook + REST API)
# or the arq scan worker, selected by the compose command. entrygraph never
# executes analyzed code, so the container can run unprivileged with a read-only
# root filesystem and no inbound egress except the git fetch.
#
# The operator dashboard is now the unified web app (`entrygraph serve`); this
# image serves the webhook receiver and the token-guarded REST API only.

FROM python:3.13-slim

RUN useradd --create-home --uid 10001 sentinel
WORKDIR /app

COPY . /src

# install with the sentinel extra (FastAPI, arq, redis, psycopg, dulwich,
# pyjwt[crypto], uvicorn)
RUN pip install --no-cache-dir "/src[sentinel]" && rm -rf /src

USER sentinel

# web process (webhook + REST API); the worker overrides this in compose
# with: entrygraph sentinel worker
CMD ["entrygraph", "sentinel", "serve", "--host", "0.0.0.0", "--port", "8000"]
