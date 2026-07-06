# entrygraph unified web app image.
#
# Stage 1 builds the SPA (webapp/) into the server package's static dir; stage 2
# is a slim Python runtime with the `server` extra (FastAPI, uvicorn, authlib,
# psycopg). `entrygraph serve` binds 0.0.0.0:8100 and serves the API + SPA.
#
# Runtime config is env-driven (EG_*): point EG_APP_DATABASE_URL at Postgres for
# durable state, keep the graph DB (EG_DB) on a mounted volume, and set the
# EG_OIDC_* vars for Authentik SSO. See docs/RUNBOOK or the server config module.

# --- stage 1: build the SPA ---
FROM node:20-slim AS webui
WORKDIR /w
COPY webapp/package.json webapp/package-lock.json ./webapp/
RUN cd webapp && npm ci
COPY webapp ./webapp
# outDir is ../src/entrygraph/server/static, so stage the package tree it targets
COPY pyproject.toml README.md ./
COPY src ./src
RUN cd webapp && npm run build   # emits into /w/src/entrygraph/server/static

# --- stage 2: python runtime ---
FROM python:3.13-slim AS runtime

RUN useradd --create-home --uid 10001 eg
WORKDIR /app

# git is needed at runtime: UI-triggered indexing clones repos via fs/remote
RUN apt-get update \
    && apt-get install --no-install-recommends -y git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# install the package (with the built SPA baked in) + the server extra
COPY --from=webui /w/pyproject.toml /w/README.md /src/
COPY --from=webui /w/src /src/src
RUN pip install --no-cache-dir "/src[server]" && rm -rf /src

# graph DB + server-side clones live under here; mount a volume at /data
ENV EG_DB=/data/graph.db \
    EG_CLONE_DIR=/data/clones \
    EG_HOST=0.0.0.0 \
    EG_PORT=8100
RUN mkdir -p /data && chown eg:eg /data
VOLUME ["/data"]

USER eg
EXPOSE 8100

# EG_APP_DATABASE_URL (Postgres), EG_BASE_URL, and EG_OIDC_* come from the
# deployment's env/Secret; auth mode flips to oidc automatically when the issuer
# is set. No CMD args — all config is env-driven.
CMD ["entrygraph", "serve"]
