# Базовый образ с CUDA 12.1 и cuDNN — совместим с PyTorch 2.3
FROM pytorch/pytorch:2.3.0-cuda12.1-cudnn8-devel

RUN apt-get update && apt-get install -y \
    git wget curl vim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Зависимости для QLoRA fine-tuning
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# train.py добавим на Шаге 12
EXPOSE 6006  # TensorBoard (опционально)
