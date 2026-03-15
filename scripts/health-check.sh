#!/bin/bash
#
# OpenClaw Health Check Script
# Checks system health, OpenClaw status, AWS resources, and connectivity
#
# Usage: ./health-check.sh
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "🦞 OpenClaw Health Check"
echo "========================"
echo ""

# Function to print status
print_status() {
    local status=$1
    local message=$2
    
    case $status in
        "ok")
            echo -e "${GREEN}✓${NC} $message"
            ;;
        "warn")
            echo -e "${YELLOW}⚠${NC} $message"
            ;;
        "error")
            echo -e "${RED}✗${NC} $message"
            ;;
        *)
            echo "  $message"
            ;;
    esac
}

# Check system resources
echo "📊 System Resources"
echo "-------------------"

# CPU
CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | sed "s/.*, *\([0-9.]*\)%* id.*/\1/" | awk '{print 100 - $1}')
if (( $(echo "$CPU_USAGE < 80" | bc -l) )); then
    print_status "ok" "CPU Usage: ${CPU_USAGE}%"
else
    print_status "warn" "CPU Usage: ${CPU_USAGE}% (high)"
fi

# Memory
MEM_INFO=$(free -m | awk 'NR==2{printf "%.1f%%", $3*100/$2 }')
MEM_USAGE=$(echo $MEM_INFO | sed 's/%//')
if (( $(echo "$MEM_USAGE < 80" | bc -l) )); then
    print_status "ok" "Memory Usage: $MEM_INFO"
else
    print_status "warn" "Memory Usage: $MEM_INFO (high)"
fi

# Disk
DISK_USAGE=$(df -h / | awk 'NR==2{print $5}' | sed 's/%//')
if (( $DISK_USAGE < 80 )); then
    print_status "ok" "Disk Usage: ${DISK_USAGE}%"
else
    print_status "warn" "Disk Usage: ${DISK_USAGE}% (high)"
fi

echo ""

# Check OpenClaw service
echo "🦞 OpenClaw Service"
echo "-------------------"

if systemctl is-active --quiet openclaw-gateway; then
    print_status "ok" "OpenClaw Gateway: Running"
    
    # Get PID and uptime
    PID=$(systemctl show openclaw-gateway -p MainPID --value)
    UPTIME=$(ps -p $PID -o etime= 2>/dev/null | tr -d ' ' || echo "unknown")
    echo "  PID: $PID, Uptime: $UPTIME"
else
    print_status "error" "OpenClaw Gateway: Not running"
fi

echo ""

# Check OpenClaw status
echo "🔍 OpenClaw Status"
echo "------------------"

if command -v openclaw &> /dev/null; then
    print_status "ok" "OpenClaw CLI: Available"
    
    # Get version
    VERSION=$(openclaw --version 2>/dev/null | head -1 || echo "unknown")
    echo "  Version: $VERSION"
    
    # Quick status check
    if openclaw status &> /dev/null; then
        print_status "ok" "OpenClaw: Responsive"
    else
        print_status "warn" "OpenClaw: Not responsive"
    fi
else
    print_status "error" "OpenClaw CLI: Not found"
fi

echo ""

# Check connectivity
echo "🌐 Network Connectivity"
echo "-----------------------"

# Check internet
if ping -c 1 8.8.8.8 &> /dev/null; then
    print_status "ok" "Internet: Connected"
else
    print_status "error" "Internet: Not connected"
fi

# Check AWS API
if aws sts get-caller-identity &> /dev/null; then
    print_status "ok" "AWS API: Accessible"
    ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
    echo "  Account: $ACCOUNT_ID"
else
    print_status "error" "AWS API: Not accessible"
fi

# Check Bedrock
if aws bedrock list-foundation-models --region $(aws configure get region) &> /dev/null 2>&1; then
    print_status "ok" "Bedrock API: Accessible"
else
    print_status "warn" "Bedrock API: Not accessible (may need model access)"
fi

echo ""

# Check security
echo "🔒 Security Status"
echo "------------------"

# Check firewall
if command -v ufw &> /dev/null; then
    if sudo ufw status | grep -q "Status: active"; then
        print_status "ok" "Firewall (UFW): Enabled"
    else
        print_status "warn" "Firewall (UFW): Disabled"
    fi
else
    print_status "info" "Firewall (UFW): Not installed"
fi

# Check OpenClaw security
SECURITY_OUTPUT=$(openclaw security audit 2>/dev/null || echo "")
if echo "$SECURITY_OUTPUT" | grep -q "0 critical"; then
    print_status "ok" "OpenClaw Security: No critical issues"
else
    print_status "warn" "OpenClaw Security: Has warnings (run: openclaw security audit)"
fi

echo ""

# Check channels
echo "📱 Messaging Channels"
echo "---------------------"

CHANNEL_STATUS=$(openclaw status 2>/dev/null | grep -A 10 "Channels" || echo "")

if echo "$CHANNEL_STATUS" | grep -q "Telegram.*OK"; then
    print_status "ok" "Telegram: Connected"
elif echo "$CHANNEL_STATUS" | grep -q "Telegram.*SETUP"; then
    print_status "warn" "Telegram: Not configured"
else
    print_status "info" "Telegram: Status unknown"
fi

if echo "$CHANNEL_STATUS" | grep -q "WhatsApp.*OK"; then
    print_status "ok" "WhatsApp: Connected"
elif echo "$CHANNEL_STATUS" | grep -q "WhatsApp.*WARN\|SETUP"; then
    print_status "warn" "WhatsApp: Not configured"
else
    print_status "info" "WhatsApp: Status unknown"
fi

echo ""

# Summary
echo "📋 Summary"
echo "----------"
echo "System health check completed."
echo ""
echo "For detailed status, run:"
echo "  openclaw status --all"
echo ""
echo "For security audit, run:"
echo "  openclaw security audit --deep"
echo ""
