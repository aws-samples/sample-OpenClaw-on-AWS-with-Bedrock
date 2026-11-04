#!/bin/sh
set -eu

EXPECTED_NODE="$1"
EXPECTED_OPENCLAW="$2"
EXPECTED_PROVIDER="$3"

[ "$(node --version)" = "v${EXPECTED_NODE}" ]
openclaw --version | grep -F "OpenClaw ${EXPECTED_OPENCLAW}"
openclaw agent --help | grep -F -- "--json"
npm --version >/dev/null

python3 - "$EXPECTED_PROVIDER" <<'PY'
import glob, json, os, sys
paths = glob.glob('/root/.openclaw/npm/projects/*/node_modules/@openclaw/amazon-bedrock-provider/package.json')
if not paths:
    raise SystemExit('Bedrock provider is not installed')
provider_path = paths[0]
if not os.path.isdir(os.path.join(os.path.dirname(provider_path), 'node_modules', 'openclaw')):
    raise SystemExit('Bedrock provider peer dependency link is missing')
with open(provider_path, encoding='utf-8') as handle:
    actual = json.load(handle).get('version')
if actual != sys.argv[1]:
    raise SystemExit(f'Bedrock provider version mismatch: {actual}')
PY

mkdir -p /root/.openclaw
sed -e 's|${AWS_REGION}|us-east-1|g' \
    -e 's|${BEDROCK_MODEL_ID}|zai.glm-5|g' \
    /app/openclaw.json > /root/.openclaw/openclaw.json

openclaw gateway --port 18789 > /tmp/openclaw-gateway-smoke.log 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 150); do
    if grep -Fq '[gateway] ready' /tmp/openclaw-gateway-smoke.log; then
        exit 0
    fi
    kill -0 "$pid" 2>/dev/null || break
    sleep 1
done

cat /tmp/openclaw-gateway-smoke.log >&2
exit 1