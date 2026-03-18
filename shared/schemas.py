from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum


class BlockType(str, Enum):
    TEXT = "text"
    TABLE         = "table"
    TABLE_SIMPLE  = "table_simple"
    TABLE_COMPLEX = "table_complex"
    FIGURE = "figure"
    FORMULA = "formula"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class BoundingBox(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float = Field(ge=0.0, le=1.0)


class BlockResult(BaseModel):
    block_id: str
    doc_id: str
    page_num: int
    block_type: BlockType
    bbox: BoundingBox
    image_path: str
    output: Optional[str] = None       # Текст / HTML / LaTeX / описание
    output_format: Optional[str] = None  # "text" / "html" / "latex" / "markdown"
    model_used: Optional[str] = None
    confidence: Optional[float] = None
    status: ProcessingStatus = ProcessingStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    error: Optional[str] = None


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    page_count: int
    status: ProcessingStatus
    created_at: datetime = Field(default_factory=datetime.utcnow)
    total_blocks: int = 0
    processed_blocks: int = 0


class TrainingPair(BaseModel):
    pair_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    block_type: BlockType
    source_doc: str
    source_page: int
    bbox: list[int] = Field(description="[x1, y1, x2, y2]")
    image_path: str
    local_model_output: dict
    target_output: dict
    quality_assessment: Optional[dict] = None
    used_in_training: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    version: str = "1.1.0"
    models_loaded: dict[str, bool] = {}
    gpu_memory_used_gb: Optional[float] = None
    gpu_memory_total_gb: Optional[float] = None
