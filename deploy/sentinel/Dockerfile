# Sentinel service image (#126). Runs either the web process (webhook + REST API +
# dashboard) or the arq scan worker, selected by the compose command. entrygraph
# never executes analyzed code, so the container can run unprivileged with a
# read-only root filesystem and no inbound egress except the git fetch.

# --- stage 1: build the React dashboard into the package's static dir ---
FROM node:20-slim AS ui
WORKDIR /w
COPY frontend ./frontend
COPY src ./src
RUN cd frontend && npm ci && npm run build

# --- stage 2: the Python service ---
FROM python:3.13-slim

RUN useradd --create-home --uid 10001 sentinel
WORKDIR /app

# bring in the source, then overlay the built dashboard so the wheel includes it
COPY . /src
COPY --from=ui /w/src/entrygraph/sentinel/static /src/src/entrygraph/sentinel/static

# install with the sentinel extra (FastAPI, arq, redis, psycopg, dulwich,
# pyjwt[crypto], uvicorn)
RUN pip install --no-cache-dir "/src[sentinel]" && rm -rf /src

USER sentinel

# web process (serves the dashboard at /ui); the worker overrides this in compose
# with: entrygraph sentinel worker
CMD ["entrygraph", "sentinel", "serve", "--host", "0.0.0.0", "--port", "8000"]
