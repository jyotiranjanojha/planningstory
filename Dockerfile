# Enterprise Production Dockerfile for Planning Story (IFSP V2)
# Single-origin container: Node.js/Express Gateway (port 3000) + internal Python FastAPI/DuckDB backend (127.0.0.1:8001).
ARG BASE_IMAGE=node:20-bookworm-slim
FROM ${BASE_IMAGE}

ARG APP_VERSION=2.0.0
ARG HARBOR_REGISTRY=amr-registry-pre.caas.intel.com
ARG HARBOR_PROJECT=if-pdh-ai-assist
ARG IMAGE_NAME=planningstory

LABEL org.opencontainers.image.title="Intel Foundry Planning Story" \
      org.opencontainers.image.description="Supply Planning AI Assistant (Node Gateway + FastAPI DuckDB V2 Core)" \
      org.opencontainers.image.source="${HARBOR_REGISTRY}/${HARBOR_PROJECT}/${IMAGE_NAME}" \
      org.opencontainers.image.version="${APP_VERSION}"

# Intel corporate proxy args (overrideable at build time via --build-arg)
ARG HTTP_PROXY="http://proxy-dmz.intel.com:912"
ARG HTTPS_PROXY="http://proxy-dmz.intel.com:912"
ARG NO_PROXY="localhost,127.0.0.0/8,10.0.0.0/8,.intel.com"
ARG NPM_REGISTRY="https://pixi.intel.com/"

ENV http_proxy="${HTTP_PROXY}" \
    https_proxy="${HTTPS_PROXY}" \
    no_proxy="${NO_PROXY}" \
    HTTP_PROXY="${HTTP_PROXY}" \
    HTTPS_PROXY="${HTTPS_PROXY}" \
    NO_PROXY="${NO_PROXY}" \
    NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BACKEND_HOST=127.0.0.1 \
    BACKEND_PORT=8001 \
    PYTHON_BASE_URL=http://127.0.0.1:8001 \
    V2_CORE_ENABLED=true \
    LLM_PROVIDER=ollama \
    SEMANTIC_MODE=legacy

WORKDIR /app

# Install Python 3 runtime and minimal system tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    bash \
    curl \
    ca-certificates \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create isolated Python venv inside /opt/venv (avoids Debian PEP 668 externally-managed errors)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Install Node dependencies first for layer caching
COPY package*.json ./
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi \
    && npm ci --omit=dev --no-fund --no-audit \
    && npm cache clean --force

# Install Python dependencies
COPY webapp/requirements.txt ./webapp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r ./webapp/requirements.txt

# Copy application source
COPY . .

# Create non-root enterprise user (UID 10001) and writable runtime directories for Rancher Pod Security
RUN groupadd --gid 10001 appgroup \
    && useradd --uid 10001 --gid 10001 --shell /bin/bash --no-create-home appuser \
    && mkdir -p /app/data /app/logs /app/reports /app/.rag \
    && chmod +x /app/start-services.sh /app/healthcheck.sh \
    && chown -R 10001:10001 /app

USER 10001:10001

# Only expose the single-origin Node Gateway externally; Python stays internal on 127.0.0.1:8001
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
  CMD ["/app/healthcheck.sh"]

CMD ["/app/start-services.sh"]
