# =============================================================================
# Executive Agent Container
#
# Extends the standard Agent Container with:
#   - Full tool permissions (shell, browser, code_execution, file_write)
#   - Claude Sonnet 4.6 as default model (highest capability)
#   - All enterprise skills pre-installed
#   - Designed for C-Suite / Senior Leadership roles
#
# IAM Role: agentcore-exec-role
#   - Full S3 access (all buckets)
#   - All Bedrock models
#   - Cross-department DynamoDB read
#
# Build:
#   docker build --platform linux/arm64 \
#     --build-arg OPENCLAW_VERSION=2026.10.4 \
#     --build-arg NODE_VERSION=24.16.0 \
#     -f exec-agent/Dockerfile \
#     -t <ECR_URI>:openclaw-2026.10.4-exec .
# =============================================================================

# Stage 1: Builder
FROM --platform=linux/arm64 python:3.12-slim AS builder

ARG TARGETARCH=arm64
ARG NODE_VERSION=24.16.0
ARG OPENCLAW_VERSION=2026.10.4
ARG CLAWHUB_VERSION=0.23.3

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip git xz-utils && rm -rf /var/lib/apt/lists/*

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

# Pin OpenClaw, Node.js, and ClawHub. Never use @latest in a production image.
RUN npm install -g "openclaw@${OPENCLAW_VERSION}" "clawhub@${CLAWHUB_VERSION}" \
    && openclaw --version | grep -F "OpenClaw ${OPENCLAW_VERSION}" \
    && clawhub --cli-version | grep -F "${CLAWHUB_VERSION}" \
    && openclaw agent --help | grep -F -- "--json"

# Executive tier: all skills pre-installed (no S3 hot-load needed)
# Full tool set: shell, browser, file, code_execution, all integrations
ARG SKILLS_PREINSTALL="jina-reader deep-research-pro transcript gog summarize notion github himalaya slack firecrawl self-improving-agent api-gateway skill-vetter s3-files excel-gen sap-connector crm-query email-send calendar-check aws-cli"
RUN HOME=/root && mkdir -p /root/.openclaw && \
    for skill in $SKILLS_PREINSTALL; do \
      for attempt in 1 2 3; do \
        clawhub install "$skill" --no-input --force && break; \
        echo "Retry $attempt for $skill..."; sleep 5; \
      done; \
    done; echo "Executive skills installed"

RUN mkdir -p /app/docs/reference && \
    TEMPLATE_DIR=$(find /usr/lib/node_modules /usr/local/lib/node_modules -path "*/openclaw/docs/reference/templates" -type d 2>/dev/null | head -1) && \
    if [ -n "$TEMPLATE_DIR" ]; then cp -r "$TEMPLATE_DIR" /app/docs/reference/templates; \
    else mkdir -p /app/docs/reference/templates; fi

COPY agent-container/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN mkdir -p /app/.compile-cache && \
    NODE_COMPILE_CACHE=/app/.compile-cache OPENCLAW_SKIP_ONBOARDING=1 \
    openclaw agent --help > /dev/null 2>&1 || true

# Stage 2: Runtime
FROM --platform=linux/arm64 python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir awscli boto3 requests

COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
RUN ln -sf /usr/lib/node_modules/openclaw/openclaw.mjs /usr/local/bin/openclaw
COPY --from=builder /app/.compile-cache /app/.compile-cache
COPY --from=builder /app/docs/reference /app/docs/reference
COPY --from=builder /skills /root/.openclaw/skills

WORKDIR /app

COPY agent-container/server.py .
COPY agent-container/entrypoint.sh .
COPY exec-agent/openclaw.json .
COPY agent-container/permissions.py .
COPY agent-container/identity.py .
COPY agent-container/memory.py .
COPY agent-container/observability.py .
COPY agent-container/safety.py .
COPY agent-container/skill_loader.py .
COPY agent-container/workspace_assembler.py .

RUN mkdir -p /app/auth-agent
COPY auth-agent/permission_request.py /app/auth-agent/permission_request.py
COPY auth-agent/__init__.py /app/auth-agent/__init__.py

RUN mkdir -p /tmp/openclaw/sessions /tmp/workspace /root/.openclaw
RUN chmod +x /app/entrypoint.sh

ENV OPENCLAW_SKIP_ONBOARDING=1
ENV PORT=8080
# Executive default: Claude Sonnet 4.6, overridable via AgentCore env var
ENV BEDROCK_MODEL_ID=global.anthropic.claude-sonnet-4-6

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
