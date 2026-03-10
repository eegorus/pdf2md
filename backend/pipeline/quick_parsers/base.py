from abc import ABC, abstractmethod
from pathlib import Path


class BaseParser(ABC):
    name: str            # "marker"
    label: str           # "Marker (локальный)"
    description: str     # короткое описание
    needs_api_key: bool  # требует ли API key

    @abstractmethod
    def is_available(self) -> bool:
        """Проверяем что пакет установлен и модели загружены"""

    @abstractmethod
    def run(self, pdf_path: str | Path, **kwargs) -> str:
        """Возвращает итоговый Markdown"""
