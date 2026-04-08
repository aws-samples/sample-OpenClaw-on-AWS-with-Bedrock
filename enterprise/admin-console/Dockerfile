# OpenClaw Admin Console — Multi-stage build
# Stage 1: Build React frontend
# Stage 2: Python backend + static frontend

FROM node:22-slim AS frontend
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src/ src/
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim
WORKDIR /opt/admin-console

# Install system deps for kubernetes-asyncio and AWS CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl unzip && \
    curl -sL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o /tmp/awscli.zip && \
    unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscli.zip && \
    apt-get purge -y unzip && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy backend
COPY server/ server/

# Copy built frontend
COPY --from=frontend /build/dist/ dist/

# start.sh reads SSM secrets and starts the server
COPY start.sh start.sh
RUN chmod +x start.sh

EXPOSE 8099

CMD ["./start.sh"]
