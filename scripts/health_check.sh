#!/usr/bin/env bash
# Smoke test for the Vault AI Backend
# Usage: scripts/health_check.sh [base_url]
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"

echo "Checking Vault AI Backend at ${BASE_URL}..."

# Health endpoint (no auth required)
HEALTH=$(curl -sf "${BASE_URL}/vault/health" 2>&1) || {
    echo "FAIL: /vault/health is unreachable"
    exit 1
}

STATUS=$(echo "$HEALTH" | python3 -c "import sys, json; print(json.load(sys.stdin)['status'])")
VLLM=$(echo "$HEALTH" | python3 -c "import sys, json; print(json.load(sys.stdin)['vllm_status'])")

echo "  Health status: ${STATUS}"
echo "  vLLM status:   ${VLLM}"

if [ "$STATUS" = "ok" ]; then
    echo "PASS: Backend is healthy"
    exit 0
else
    echo "WARN: Backend is degraded (vLLM may be starting)"
    exit 1
fi
