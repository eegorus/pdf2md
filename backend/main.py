import os
import sys
import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import torch
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# shared/ монтируется как /app/shared через docker-compose volume
sys.path.insert(0, "/app")
from shared.schemas import HealthResponse, ProcessingStatus

# ── Логирование ───────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("prms.main")


# ═══════════════════════════════════════════════════════════════════════
# MODEL REGISTRY — единое хранилище всех загруженных моделей
# Используется как module-level singleton: from main import models
# ═══════════════════════════════════════════════════════════════════════
class ModelRegistry:
    def __init__(self):
        # DocLayout-YOLO
        self.layout_model = None

        # EasyOCR
        self.ocr_reader = None

        # dots.ocr (таблицы)
        self.table_model = None
        self.table_processor = None

        # Статусы загрузки — используются в /health
        self.status: dict[str, bool] = {
            "layout_yolo": False,
            "easyocr":     False,
            "dots_ocr":    False,
            "texteller":   False,   # CLI-инструмент
            "ollama":      False,   # HTTP-сервис
        }

    def get_gpu_info(self) -> dict | None:
        if not torch.cuda.is_available():
            return None
        props = torch.cuda.get_device_properties(0)
        used  = torch.cuda.memory_allocated(0) / 1024**3
        total = props.total_memory / 1024**3
        return {
            "name":       props.name,
            "used_gb":    round(used, 2),
            "total_gb":   round(total, 2),
            "free_gb":    round(total - used, 2),
        }

    def overall_status(self) -> str:
        """ok / degraded / error"""
        loaded = sum(self.status.values())
        total  = len(self.status)
        if loaded == total:
            return "ok"
        if loaded >= 3:           # критичные модели работают
            return "degraded"
        return "error"


# Module-level instance — импортируется в роутерах
models = ModelRegistry()

# In-memory хранилище статусов задач обработки
# { job_id: {"status": "...", "doc_id": "...", "progress": 0} }
processing_jobs: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════════
# LIFESPAN — загрузка моделей при старте, очистка при завершении
# Современный способ (вместо устаревшего @app.on_event)
# ═══════════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 55)
    logger.info("  PRMS Backend — загрузка моделей")
    logger.info("=" * 55)

    MODELS_DIR = Path(os.getenv("MODELS_DIR", "/app/models"))

    # ── 1. DocLayout-YOLO ─────────────────────────────────────
    try:
        from doclayout_yolo import YOLOv10
        model_file = MODELS_DIR / "doclayout-yolo" / \
            "doclayout_yolo_docstructbench_imgsz1024.pt"

        if not model_file.exists():
            raise FileNotFoundError(f"Файл не найден: {model_file}")

        models.layout_model = YOLOv10(str(model_file))
        models.status["layout_yolo"] = True
        logger.info("✅ DocLayout-YOLO загружен")
    except Exception as e:
        logger.error(f"❌ DocLayout-YOLO: {e}")

    # ── 2. EasyOCR ────────────────────────────────────────────
    try:
        import easyocr
        # Модели берутся из /root/.EasyOCR (easyocr-cache volume)
        # download_enabled=False — скачивание должно быть уже сделано
        # Если вдруг нет — ставим True, но это замедлит старт
        models.ocr_reader = easyocr.Reader(
            ["ru", "en"],
            gpu=True,
            download_enabled=True,
            verbose=False,
        )
        models.status["easyocr"] = True
        logger.info("✅ EasyOCR (ru+en) загружен")
    except Exception as e:
        logger.error(f"❌ EasyOCR: {e}")

    # ── 3. dots.ocr ───────────────────────────────────────────
    try:
        # AutoModelForCausalLM + trust_remote_code=True загружает кастомный
        # класс DotsOCRForCausalLM прямо из скачанного репо (auto_map в config.json)
        # AutoModelForImageTextToText не знает про DotsOCRConfig — не использовать
        from transformers import AutoProcessor, AutoModelForCausalLM

        dots_path = str(MODELS_DIR / "dots-ocr")
        if not (MODELS_DIR / "dots-ocr" / "config.json").exists():
            raise FileNotFoundError(f"dots.ocr не скачан: {dots_path}")

        logger.info("⏳ Загружаем dots.ocr (~4 ГБ, займёт ~30 сек)...")

        models.table_processor = AutoProcessor.from_pretrained(
            dots_path,
            trust_remote_code=True,
            use_fast=True,
        )
        models.table_model = AutoModelForCausalLM.from_pretrained(
            dots_path,
            torch_dtype=torch.bfloat16,
            attn_implementation="eager",  # без flash-attn — не нужна компиляция
            trust_remote_code=True,
            device_map="cuda",
        )
        models.table_model.eval()
        models.status["dots_ocr"] = True

        gpu = models.get_gpu_info()
        logger.info(f"✅ dots.ocr загружен | VRAM: {gpu['used_gb']:.1f}/{gpu['total_gb']:.1f} ГБ")
    except Exception as e:
        logger.error(f"❌ dots.ocr: {e}")

    # ── 4. TexTeller (CLI) ────────────────────────────────────
    try:
        result = subprocess.run(
            ["texteller", "--help"],
            capture_output=True,
            timeout=10,
        )
        models.status["texteller"] = (result.returncode == 0)
        if models.status["texteller"]:
            logger.info("✅ TexTeller CLI доступен")
        else:
            logger.warning(f"⚠️  TexTeller вернул код {result.returncode}")
    except FileNotFoundError:
        logger.error("❌ TexTeller: команда не найдена в PATH")
    except Exception as e:
        logger.error(f"❌ TexTeller: {e}")

    # ── 5. Ollama ─────────────────────────────────────────────
    try:
        ollama_url   = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        fallback_mdl = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5vl:7b")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{ollama_url}/api/tags")
            if resp.status_code == 200:
                available = [m["name"] for m in resp.json().get("models", [])]
                model_ok  = any(fallback_mdl in m for m in available)
                models.status["ollama"] = model_ok

                if model_ok:
                    logger.info(f"✅ Ollama: {fallback_mdl} готов")
                else:
                    logger.warning(
                        f"⚠️  Ollama работает, но {fallback_mdl} не найден. "
                        f"Доступны: {available}. Выполни: make pull-model"
                    )
    except Exception as e:
        logger.error(f"❌ Ollama недоступен: {e}")

    # ── Итоговый отчёт ────────────────────────────────────────
    loaded = sum(models.status.values())
    total  = len(models.status)
    gpu    = models.get_gpu_info()
    logger.info("=" * 55)
    logger.info(f"  Моделей загружено: {loaded}/{total}")
    if gpu:
        logger.info(
            f"  GPU: {gpu['name']} | "
            f"VRAM: {gpu['used_gb']:.1f}/{gpu['total_gb']:.1f} ГБ "
            f"(свободно: {gpu['free_gb']:.1f} ГБ)"
        )
    logger.info("=" * 55)

    yield  # ← Приложение работает

    # ── Shutdown: освобождаем VRAM ────────────────────────────
    logger.info("Выгрузка моделей из памяти...")
    models.layout_model   = None
    models.ocr_reader     = None
    models.table_model    = None
    models.table_processor = None

    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

    logger.info("✅ VRAM освобождена")


# ═══════════════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════════════
app = FastAPI(
    title="PRMS Table Extractor API",
    description=(
        "PDF Recognition and Model Training Service.\n\n"
        "**Workflow:** Upload PDF → Detect blocks → OCR → Review → Fine-tune"
    ),
    version="1.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS: frontend обращается к backend по имени сервиса внутри Docker-сети
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8501",
        "http://frontend:8501",
        "http://localhost:3000",   # на случай dev-сервера
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЕ ОБРАБОТЧИКИ ОШИБОК
# ═══════════════════════════════════════════════════════════════════════
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "type": type(exc).__name__},
    )


# ═══════════════════════════════════════════════════════════════════════
# SYSTEM ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════
@app.get("/", tags=["System"], summary="Root")
async def root():
    return {
        "service": "PRMS Table Extractor",
        "version": "1.1.0",
        "docs":    "/docs",
        "health":  "/health",
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Healthcheck — статус моделей и GPU",
)
async def health():
    """
    Возвращает статус всех моделей и состояние GPU.
    Используется Docker healthcheck и frontend для отображения статуса.
    """
    gpu = models.get_gpu_info()
    return HealthResponse(
        status=models.overall_status(),
        models_loaded=models.status,
        gpu_memory_used_gb=gpu["used_gb"]   if gpu else None,
        gpu_memory_total_gb=gpu["total_gb"] if gpu else None,
    )


@app.get("/health/ollama", tags=["System"], summary="Проверка связи с Ollama")
async def health_ollama():
    """Отдельная проверка Ollama — полезна для диагностики."""
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp  = await client.get(f"{ollama_url}/api/tags")
            mdls  = [m["name"] for m in resp.json().get("models", [])]
            return {"status": "ok", "url": ollama_url, "models": mdls}
    except Exception as e:
        return {"status": "error", "url": ollama_url, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# РОУТЕРЫ (skeleton — логика добавляется на шагах 7–9)
# ═══════════════════════════════════════════════════════════════════════
from routers import documents, processing, training, quick   # noqa: E402

app.include_router(
    documents.router,
    prefix="/documents",
    tags=["Documents"],
)
app.include_router(quick.router)
app.include_router(
    processing.router,
    prefix="/processing",
    tags=["Processing"],
)
app.include_router(
    training.router,
    prefix="/training",
    tags=["Training"],
)
