#!/bin/bash
echo "=== Проверка структуры проекта PRMS ==="

REQUIRED=(
  "docker-compose.yml"
  ".env"
  ".gitignore"
  "shared/schemas.py"
  "shared/utils.py"
  "backend/main.py"
  "backend/pipeline/pdf_splitter.py"
  "backend/pipeline/layout_detector.py"
  "frontend/app.py"
  "frontend/pages/1_Upload.py"
  "data/uploads"
  "data/training/images"
  "models/versions"
)

ALL_OK=true
for item in "${REQUIRED[@]}"; do
  if [ -e "$item" ]; then
    echo "  ✅ $item"
  else
    echo "  ❌ ОТСУТСТВУЕТ: $item"
    ALL_OK=false
  fi
done

if $ALL_OK; then
  echo ""
  echo "✅ Все файлы и директории на месте!"
else
  echo ""
  echo "❌ Есть проблемы — исправь перед следующим шагом"
fi
