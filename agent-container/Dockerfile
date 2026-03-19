# =============================================================================
# OpenClaw Agent Container for Bedrock AgentCore Runtime
# Multi-stage build: builder (install everything) -> runtime (minimal image)
# =============================================================================

# Stage 1: Builder - install all dependencies and tools
FROM --platform=linux/arm64 python:3.12-slim AS builder

ARG TARGETARCH=arm64

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip git && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2 (architecture-aware)
RUN if [ "$TARGETARCH" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then \
        curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "/tmp/awscli.zip"; \
    else \
        curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscli.zip"; \
    fi && unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscli.zip

# Install Node.js 22 (required by OpenClaw)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs && rm -rf /var/lib/apt/lists/*

# Install OpenClaw globally via npm
RUN npm install -g openclaw@latest

# Symlink templates so OpenClaw can find them from any cwd
RUN TEMPLATE_DIR=$(find /usr/local/lib/node_modules -path "*/openclaw/docs/reference/templates" -type d 2>/dev/null | head -1) \
    && if [ -n "$TEMPLATE_DIR" ]; then mkdir -p /app/docs/reference && ln -sf "$TEMPLATE_DIR" /app/docs/reference/templates; fi

# Install Python dependencies
COPY agent-container/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-warm V8 Compile Cache (Node.js 22+)
# Caches compiled bytecode so openclaw CLI modules load faster at runtime
RUN mkdir -p /app/.compile-cache && \
    NODE_COMPILE_CACHE=/app/.compile-cache OPENCLAW_SKIP_ONBOARDING=1 \
    openclaw agent --help > /dev/null 2>&1 || true

# Stage 2: Runtime - minimal image with only needed artifacts
FROM --platform=linux/arm64 python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

# Copy AWS CLI from builder
COPY --from=builder /usr/local/aws-cli /usr/local/aws-cli
COPY --from=builder /usr/local/bin/aws /usr/local/bin/aws

# Copy Node.js runtime + global modules from builder
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules

# Re-create openclaw symlink (COPY resolves symlinks, breaking ESM imports)
RUN ln -sf /usr/local/lib/node_modules/openclaw/openclaw.mjs /usr/local/bin/openclaw

# Copy Python site-packages (boto3, requests)
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages

# Copy V8 compile cache + templates from builder
COPY --from=builder /app/.compile-cache /app/.compile-cache
COPY --from=builder /app/docs /app/docs

WORKDIR /app

# Copy Agent Container application files
COPY agent-container/server.py .
COPY agent-container/entrypoint.sh .
COPY agent-container/openclaw.json .
COPY agent-container/permissions.py .
COPY agent-container/identity.py .
COPY agent-container/memory.py .
COPY agent-container/observability.py .
COPY agent-container/safety.py .

# Copy auth-agent module
RUN mkdir -p /app/auth-agent
COPY auth-agent/permission_request.py /app/auth-agent/permission_request.py
COPY auth-agent/__init__.py /app/auth-agent/__init__.py

# Create directories
RUN mkdir -p /tmp/openclaw/sessions /tmp/workspace /root/.openclaw

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

ENV OPENCLAW_SKIP_ONBOARDING=1
ENV PORT=8080

EXPOSE 8080

# Build from repo root: docker build --platform linux/arm64 -f agent-container/Dockerfile .
ENTRYPOINT ["/app/entrypoint.sh"]
