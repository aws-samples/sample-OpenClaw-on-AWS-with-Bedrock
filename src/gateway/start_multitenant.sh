#!/bin/bash
# Start all multi-tenant services on EC2 Gateway
# Usage: bash start_multitenant.sh [start|stop|status]

ACTION="${1:-start}"
export AWS_REGION=us-east-1
export STACK_NAME=openclaw-multitenancy

case "$ACTION" in
  stop)
    echo "Stopping services..."
    pkill -f 'bedrock_proxy' 2>/dev/null
    pkill -f 'tenant_router' 2>/dev/null
    echo "Stopped proxy + router (gateway left running)"
    ;;

  status)
    echo "=== Services ==="
    ss -tlnp | grep -E '(18789|18792|8090|8091)' || echo "No services"
    ;;

  start)
    echo "Starting multi-tenant services..."

    # 1. Bedrock Proxy (port 8091)
    pkill -f 'bedrock_proxy' 2>/dev/null
    sleep 1
    TENANT_ROUTER_URL=http://127.0.0.1:8090 PROXY_PORT=8091 \
      python3 /home/ubuntu/bedrock_proxy.py >> /tmp/bedrock_proxy.log 2>&1 &
    echo "Bedrock Proxy PID=$!"

    # 2. Tenant Router (port 8090)
    pkill -f 'tenant_router' 2>/dev/null
    sleep 1
    python3 /home/ubuntu/tenant_router.py >> /tmp/tenant_router.log 2>&1 &
    echo "Tenant Router PID=$!"

    sleep 2
    echo "=== Ports ==="
    ss -tlnp | grep -E '(8090|8091)'

    # 3. Switch OpenClaw to proxy (if gateway is running)
    if ss -tlnp | grep -q 18789; then
      echo "Gateway already running, updating baseUrl..."
      source /home/ubuntu/.nvm/nvm.sh
      openclaw config set models.providers.amazon-bedrock.baseUrl http://localhost:8091 2>/dev/null
      echo "baseUrl set to proxy. Gateway needs restart to pick up change."
      echo "Run: openclaw gateway restart"
    fi
    echo "Done"
    ;;
esac
