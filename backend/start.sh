#!/bin/bash
set -e

echo "============================================"
echo "  PRMS Backend starting..."
echo "============================================"

echo "[1/3] GPU check..."
python -c "
import torch
if torch.cuda.is_available():
    name = torch.cuda.get_device_name(0)
    vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f'  OK GPU: {name} ({vram:.1f} GB VRAM)')
else:
    print('  FAIL: GPU not available')
    exit(1)
"

echo "[2/3] Ollama check..."
for i in $(seq 1 12); do
    if curl -sf "${OLLAMA_BASE_URL:-http://ollama:11434}/api/tags" > /dev/null 2>&1; then
        echo "  OK Ollama ready"
        break
    fi
    echo "  Waiting for Ollama... ($i/12)"
    sleep 5
done

echo "[3/3] Starting uvicorn..."
echo "============================================"

LOG_LEVEL_LOWER=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')
echo "  log-level: ${LOG_LEVEL_LOWER}"

exec uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level "${LOG_LEVEL_LOWER}" \
    --timeout-keep-alive 65
