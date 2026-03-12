from .marker_parser  import MarkerParser
from .docling_parser import DoclingParser
from .pymupdf_parser import PyMuPDFParser
from .unstructured_parser import UnstructuredParser
from .cloud_parser   import LlamaParseParser, GPT4oParser, ClaudeParser

ALL_PARSERS = [
    PyMuPDFParser(),
    MarkerParser(),
    DoclingParser(),
    UnstructuredParser(),
    LlamaParseParser(),
    GPT4oParser(),
    ClaudeParser(),
]

def get_parser(name: str):
    for p in ALL_PARSERS:
        if p.name == name:
            return p
    raise ValueError(f"Парсер '{name}' не найден")
