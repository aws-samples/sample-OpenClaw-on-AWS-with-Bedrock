# Stage 1: Python 3.12 slim runtime with AWS CLI + Node.js + OpenClaw
FROM python:3.12-slim

ARG TARGETARCH=arm64

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2 (architecture-aware)
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o "/tmp/awscli.zip"; \
    else \
        curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscli.zip"; \
    fi \
    && unzip -q /tmp/awscli.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscli.zip

# Install Node.js 22 (required by OpenClaw)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install OpenClaw globally via npm
RUN npm install -g openclaw@latest

# Symlink templates so OpenClaw can find them from any cwd
RUN TEMPLATE_DIR=$(find /usr/local/lib/node_modules -path "*/openclaw/docs/reference/templates" -type d 2>/dev/null | head -1) \
    && if [ -n "$TEMPLATE_DIR" ]; then mkdir -p /app/docs/reference && ln -sf "$TEMPLATE_DIR" /app/docs/reference/templates; fi

WORKDIR /app

# Install Python dependencies
COPY agent-container/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
RUN mkdir -p /tmp/openclaw/sessions /tmp/workspace

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

ENV OPENCLAW_SKIP_ONBOARDING=1
ENV PORT=8080

EXPOSE 8080

# Build from repo root: docker build --platform linux/arm64 -f agent-container/Dockerfile .
ENTRYPOINT ["/app/entrypoint.sh"]
