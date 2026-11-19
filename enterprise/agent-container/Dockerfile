# =============================================================================
# OpenClaw Agent Container for Bedrock AgentCore Runtime
# Multi-stage build: builder (install everything) -> runtime (minimal image)
# =============================================================================

# Stage 1: Builder - install all dependencies and tools
FROM --platform=linux/arm64 python:3.12-slim AS builder

ARG TARGETARCH=arm64
ARG NODE_VERSION=24.16.0
ARG OPENCLAW_VERSION=2026.10.4
ARG CLAWHUB_VERSION=0.23.3
ARG BEDROCK_PROVIDER_VERSION=2026.10.4

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip git xz-utils && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2 (architecture-aware)
RUN if [ "$TARGETARCH" = "arm64" ] || [ "$(uname -m)" = "aarch64" ]; then \
        curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "/tmp/awscli.zip"; \
    else \
        curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscli.zip"; \
    fi && unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscli.zip

# Install an exact Node.js release compatible with the pinned OpenClaw version;
# distro packages are intentionally avoided.
RUN NODE_ARCH=$([ "$TARGETARCH" = "arm64" ] && echo arm64 || echo x64) \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version | grep -Fx "v${NODE_VERSION}"

# Pin OpenClaw, Node.js, ClawHub, and the matching Bedrock provider.
# OpenClaw discovers provider packages from its managed npm project directory.
RUN npm install -g "openclaw@${OPENCLAW_VERSION}" "clawhub@${CLAWHUB_VERSION}" \
    && PROVIDER_DIR=/root/.openclaw/npm/projects/openclaw-amazon-bedrock-provider-53c77d25cd \
    && mkdir -p "$PROVIDER_DIR" \
    && npm install --prefix "$PROVIDER_DIR" --save-exact \
        "@openclaw/amazon-bedrock-provider@${BEDROCK_PROVIDER_VERSION}" \
    && PROVIDER_PACKAGE_DIR="$PROVIDER_DIR/node_modules/@openclaw/amazon-bedrock-provider" \
    && mkdir -p "$PROVIDER_PACKAGE_DIR/node_modules" \
    && ln -s /usr/lib/node_modules/openclaw "$PROVIDER_PACKAGE_DIR/node_modules/openclaw" \
    && node -e "const p=require('$PROVIDER_DIR/node_modules/@openclaw/amazon-bedrock-provider/package.json'); if(p.version!=='${BEDROCK_PROVIDER_VERSION}') process.exit(1)" \
    && openclaw --version | grep -F "OpenClaw ${OPENCLAW_VERSION}" \
    && clawhub --cli-version | grep -F "${CLAWHUB_VERSION}" \
    && openclaw agent --help | grep -F -- "--json"

# Install enterprise built-in skills (Layer 1)
# These are available to all tenants with zero cold-start overhead.
# To customize: override SKILLS_PREINSTALL at build time.
# Only install skills that are assigned to positions and work without personal API keys.
# PRD-tools-skills-market Section 10: keep deep-research-pro + self-improving-agent.
# Add jina-reader (used by deep-research-pro internally) and skill-vetter (security review).
# Everything else needs personal credentials (github, notion, himalaya, gog, firecrawl, slack)
# or has no assignment (transcript, api-gateway, summarize).
# Fewer skills = faster Gateway startup (fewer tool definitions to register with Bedrock).
ARG SKILLS_PREINSTALL="deep-research-pro self-improving-agent jina-reader skill-vetter"
RUN HOME=/root && mkdir -p /root/.openclaw && \
    for skill in $SKILLS_PREINSTALL; do \
      for attempt in 1 2 3; do \
        clawhub install "$skill" --no-input --force && break; \
        echo "Retry $attempt for $skill..."; sleep 5; \
      done; \
    done; echo "Built-in skills installed: $SKILLS_PREINSTALL"

# Find and copy templates so OpenClaw can find them from any cwd
RUN mkdir -p /app/docs/reference && \
    TEMPLATE_DIR=$(find /usr/lib/node_modules /usr/local/lib/node_modules -path "*/openclaw/docs/reference/templates" -type d 2>/dev/null | head -1) && \
    if [ -n "$TEMPLATE_DIR" ]; then cp -r "$TEMPLATE_DIR" /app/docs/reference/templates; \
    else echo "WARNING: templates not found, creating empty dir" && mkdir -p /app/docs/reference/templates; fi

# Install Python dependencies
COPY agent-container/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Pre-warm V8 Compile Cache (supported by the pinned Node.js runtime)
# Caches compiled bytecode so openclaw CLI modules load faster at runtime
RUN mkdir -p /app/.compile-cache && \
    NODE_COMPILE_CACHE=/app/.compile-cache OPENCLAW_SKIP_ONBOARDING=1 \
    openclaw agent --help > /dev/null 2>&1 || true

# Stage 2: Runtime - minimal image with only needed artifacts
FROM --platform=linux/arm64 python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends jq curl \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI via pip (not binary copy — avoids Python version mismatch)
RUN pip install --no-cache-dir awscli boto3 requests

# Copy Node.js runtime, global modules, and the managed provider project.
# Recreate CLI symlinks because Docker COPY dereferences source symlinks.
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
COPY --from=builder /root/.openclaw/npm /root/.openclaw/npm

RUN ln -sf /usr/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -sf /usr/lib/node_modules/openclaw/openclaw.mjs /usr/local/bin/openclaw \
    && npm --version >/dev/null

# Copy V8 compile cache + templates from builder
COPY --from=builder /app/.compile-cache /app/.compile-cache
COPY --from=builder /app/docs/reference /app/docs/reference

# Copy built-in skills from builder (Layer 1)
# clawhub installs to /skills/ directory
COPY --from=builder /skills /root/.openclaw/skills

WORKDIR /app

# Copy Agent Container application files
COPY agent-container/server.py .
COPY agent-container/entrypoint.sh .
COPY agent-container/openclaw.json .
COPY agent-container/permissions.py .
COPY agent-container/identity.py .
COPY agent-container/memory.py .
COPY agent-container/observability.py .
COPY agent-container/skill_usage.py .
COPY agent-container/safety.py .
COPY agent-container/skill_loader.py .
COPY agent-container/workspace_assembler.py .
COPY agent-container/smoke-test.sh .

# Copy auth-agent module
RUN mkdir -p /app/auth-agent
COPY auth-agent/permission_request.py /app/auth-agent/permission_request.py
COPY auth-agent/__init__.py /app/auth-agent/__init__.py

# Create directories
RUN mkdir -p /tmp/openclaw/sessions /tmp/workspace /root/.openclaw

# Make runtime scripts executable
RUN chmod +x /app/entrypoint.sh /app/smoke-test.sh

ENV OPENCLAW_SKIP_ONBOARDING=1
ENV PORT=8080

EXPOSE 8080

# Build from repo root: docker build --platform linux/arm64 -f agent-container/Dockerfile .
ENTRYPOINT ["/app/entrypoint.sh"]
