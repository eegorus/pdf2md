.PHONY: up down restart logs health pull-model finetune status clean

# === Основные команды ===

up:          ## Запустить все сервисы
	docker compose up -d --build
	@echo "⏳ Ждём healthcheck (обычно ~2 минуты)..."
	@sleep 10
	@$(MAKE) status

down:        ## Остановить все сервисы
	docker compose down

restart:     ## Перезапустить все сервисы
	docker compose restart

logs:        ## Показать логи всех сервисов
	docker compose logs -f

logs-backend:   ## Логи только backend
	docker compose logs -f backend

logs-ollama:    ## Логи только ollama
	docker compose logs -f ollama

# === Управление моделями ===

pull-model:  ## Скачать qwen2.5vl:7b в Ollama (запускать после up)
	docker compose exec ollama ollama pull qwen2.5vl:7b
	@echo "✅ Модель скачана и готова к использованию"

list-models: ## Список загруженных моделей в Ollama
	docker compose exec ollama ollama list

# === Fine-tuning ===

finetune-start: ## Запустить fine-tuning (ВНИМАНИЕ: останавливает backend и ollama!)
	@echo "⚠️  Останавливаем inference-контейнеры для освобождения VRAM..."
	docker compose stop backend ollama
	@echo "🚀 Запускаем fine-tuning контейнер..."
	docker compose --profile finetune up -d finetune
	@echo "✅ Войти в контейнер: make finetune-shell"

finetune-shell: ## Зайти в fine-tuning контейнер
	docker compose --profile finetune exec finetune bash

finetune-stop: ## Остановить fine-tuning и вернуть inference
	docker compose --profile finetune stop finetune
	@echo "🔄 Возвращаем inference-контейнеры..."
	docker compose start ollama backend
	@echo "✅ Inference восстановлен"

# === Мониторинг ===

status:      ## Статус всех контейнеров
	docker compose ps

health:      ## Проверить healthcheck эндпоинты
	@echo "=== Ollama ==="
	@curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Models: {len(d[\"models\"])}')" 2>/dev/null || echo "❌ Недоступен"
	@echo "=== Backend ==="
	@curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "❌ Недоступен"
	@echo "=== Frontend ==="
	@curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8501/_stcore/health 2>/dev/null || echo "❌ Недоступен"

gpu:         ## Текущее использование GPU
	watch -n 2 nvidia-smi

# === Очистка ===

clean:       ## Удалить контейнеры и образы (данные сохраняются!)
	docker compose down --rmi local
	@echo "⚠️  Данные в data/ и named volumes сохранены"

clean-all:   ## ОПАСНО: удалить всё включая volumes с данными
	@echo "⚠️  ВНИМАНИЕ: удаляем ВСЕ данные включая модели Ollama!"
	@read -p "Вы уверены? (yes/no): " confirm && [ "$$confirm" = "yes" ]
	docker compose down --volumes --rmi local

help:        ## Показать эту справку
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
