# One build file, one image per service (build targets). Every target is disposable and configured by env only.
ARG BASE_REGISTRY=public.ecr.aws/docker/library
FROM ${BASE_REGISTRY}/python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FASTEMBED_CACHE=/opt/fastembed HF_HUB_DISABLE_SYMLINKS_WARNING=1
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY producery ./producery
# Reproducible images: third-party versions come from the lockfile (pip-compile on this base image, `make lock`); the
# package itself installs without re-resolving.
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps .
COPY config ./config
COPY prompts ./prompts
COPY fixtures ./fixtures
COPY scripts ./scripts
ARG REVISION_ID=dev
ENV REVISION_ID=$REVISION_ID CONFIG_DIR=/app/config PROMPTS_DIR=/app/prompts
RUN useradd -m -u 10001 app

# --- services needing the embedding model (baked in at build time: no runtime download, deterministic) ---
FROM base AS embed
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/opt/fastembed')" \
    && chown -R app:app /opt/fastembed

FROM embed AS agent
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=12 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)"
CMD ["uvicorn", "producery.server.app:app", "--host", "0.0.0.0", "--port", "8000"]

FROM embed AS worker
USER app
EXPOSE 8090
HEALTHCHECK --interval=15s --timeout=5s --retries=10 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=3).status==200 else 1)"
CMD ["arq", "producery.worker.main.WorkerSettings"]

FROM embed AS mcp-data
USER app
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=5s --retries=12 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/health',timeout=3).status==200 else 1)"
CMD ["python", "-m", "producery.mcp_data.server"]

FROM embed AS seed
USER app
CMD ["python", "scripts/seed.py"]

# --- mcp-exec holds no secrets beyond nothing; it only forwards to the executor ---
FROM base AS mcp-exec
USER app
EXPOSE 8082
HEALTHCHECK --interval=10s --timeout=5s --retries=12 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8082/health',timeout=3).status==200 else 1)"
CMD ["python", "-m", "producery.mcp_exec.server"]

# --- executor: a minimal image with the stdlib only — no app dependencies, no secrets, no network ---
FROM ${BASE_REGISTRY}/python:3.11-slim AS executor
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -M -u 10002 -s /usr/sbin/nologin sandbox && mkdir -p /work
COPY producery/executor/server.py /srv/executor.py
# The daemon stays root ONLY to switch each snippet to the unprivileged `sandbox` user (compose grants just SETUID/SETGID).
EXPOSE 9000
CMD ["python", "-u", "-I", "/srv/executor.py"]
