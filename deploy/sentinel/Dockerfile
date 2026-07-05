# Sentinel service image (#126). Runs either the web process (webhook + REST API)
# or the arq scan worker, selected by the compose command. entrygraph never
# executes analyzed code, so the container can run unprivileged with a read-only
# root filesystem and no inbound egress except the git fetch.
FROM python:3.13-slim

# non-root by default
RUN useradd --create-home --uid 10001 sentinel
WORKDIR /app

# install the package with the sentinel extra (FastAPI, arq, redis, psycopg,
# dulwich, pyjwt[crypto], uvicorn)
COPY . /src
RUN pip install --no-cache-dir "/src[sentinel]" && rm -rf /src

USER sentinel

# web process; the worker overrides this in compose with:
#   arq entrygraph.sentinel.queue.WorkerSettings
CMD ["uvicorn", "entrygraph.sentinel.app:build_from_env", "--factory", "--host", "0.0.0.0", "--port", "8000"]
