FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl wget \
    libglib2.0-0 libsm6 libxext6 libxrender-dev \
    libgl1-mesa-glx libgomp1 \
    poppler-utils \
    gcc g++ \
    # EasyOCR нужны эти библиотеки для работы с изображениями
    libpng-dev libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python зависимости ────────────────────────────────────────────────
# Копируем requirements.txt первым — слой кэшируется отдельно от кода
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── dots.ocr (установка пакета без весов) ─────────────────────────────
# Веса (~4 ГБ) скачиваются отдельно через models/download_models.sh
RUN git clone --depth 1 https://github.com/rednote-hilab/dots.ocr.git /tmp/dots-ocr \
    && cd /tmp/dots-ocr \
    && pip install --no-cache-dir -e . \
    && rm -rf /tmp/dots-ocr/.git

# ── Environment ───────────────────────────────────────────────────────
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# EasyOCR хранит модели здесь — маппируем на volume для переиспользования
ENV EASYOCR_MODULE_PATH=/root/.EasyOCR

# ── Копируем код ──────────────────────────────────────────────────────
COPY . .

RUN mkdir -p \
    /app/data/uploads \
    /app/data/pages \
    /app/data/blocks \
    /app/data/results \
    /app/data/training/images

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["/app/start.sh"]
