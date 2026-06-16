"""
shared/schemas.py — Pydantic модели для API

Используются для валидации входных данных и
сериализации ответов во всех роутерах.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ── Перечисления ──────────────────────────────────────────────────────

class BlockType(str, Enum):
    text           = "text"
    title          = "title"
    figurecaption  = "figurecaption"
    table          = "table"
    table_simple   = "table_simple"
    table_complex  = "table_complex"
    formula        = "formula"
    figure         = "figure"


class DocStatus(str, Enum):
    uploaded      = "uploaded"
    split_done    = "split_done"
    layout_done   = "layout_done"
    ocr_done      = "ocr_done"
    exported      = "exported"
    error         = "error"


class ExportFormat(str, Enum):
    json     = "json"
    markdown = "markdown"
    csv      = "csv"


# ── Блоки ─────────────────────────────────────────────────────────────

class BBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return self.width * self.height


class BlockResult(BaseModel):
    block_id:   str
    page_num:   int
    block_type: BlockType
    bbox:       list[int] = Field(..., min_length=4, max_length=4)
    confidence: float
    raw_class:  str
    image_path: Optional[str] = None
    status:     str
    output:     Optional[str] = None


# ── Документы ─────────────────────────────────────────────────────────

class PageInfo(BaseModel):
    page_num: int
    path:     str
    width:    int
    height:   int


class DocumentMeta(BaseModel):
    doc_id:      str
    filename:    str
    page_count:  int = 0
    status:      DocStatus = DocStatus.uploaded
    total_blocks: int = 0
    pages:       list[PageInfo] = []


class DocumentListItem(BaseModel):
    doc_id:     str
    filename:   str
    page_count: int
    status:     DocStatus


# ── API Responses ─────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    doc_id:   str
    filename: str
    size_mb:  float
    status:   str
    message:  str


class ProcessingStatusResponse(BaseModel):
    doc_id:       str
    status:       DocStatus
    page_count:   int = 0
    total_blocks: int = 0


class OCRResultsResponse(BaseModel):
    doc_id:       str
    total_blocks: int
    by_type:      dict[str, int]
    blocks:       list[BlockResult]


class ExportResponse(BaseModel):
    doc_id:      str
    format:      ExportFormat
    file_path:   str
    size_bytes:  int
    message:     str


# ── Health ────────────────────────────────────────────────────────────

class ModelsStatus(BaseModel):
    layout_yolo: bool = False
    easyocr:     bool = False
    dots_ocr:    bool = False
    texteller:   bool = False
    ollama:      bool = False


class HealthResponse(BaseModel):
    status:               str
    version:              str = "1.1.0"
    models_loaded:        ModelsStatus
    gpu_memory_used_gb:   Optional[float] = None
    gpu_memory_total_gb:  Optional[float] = None
