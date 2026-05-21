from pathlib import Path

from .base import BaseParser


class DoclingParser(BaseParser):
    name        = "docling"
    label       = "Docling (локальный 🥈)"
    description = "Отличное качество для таблиц и сложной структуры. IBM Research."
    needs_api_key = False

    def is_available(self) -> bool:
        try:
            import docling
            return True
        except ImportError:
            return False

    def _call_api(self, *args, **kwargs) -> str:
        raise NotImplementedError("Docling не использует VLM")

    def run(self, pdf_path: str | Path, **kwargs) -> str:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        pipeline_options = PdfPipelineOptions()
        pipeline_options.images_scale = 2.0
        pipeline_options.generate_page_images = False
        pipeline_options.generate_picture_images = True

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(str(pdf_path))

        try:
            from docling.cli.main import ImageRefMode
            return result.document.export_to_markdown(image_mode=ImageRefMode.EMBEDDED)
        except (ImportError, TypeError):
            return result.document.export_to_markdown()
