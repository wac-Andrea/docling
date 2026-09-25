"""Converters: turn the formats that are not a digital PDF into one, before extracting.

    from document_processor.converters import convert_to_pdf
    convert_to_pdf("informe.docx", "output/informe.pdf")

The exceptions they raise are in document_processor.errors.
"""

from .file2pdf import SUPPORTED_EXTENSIONS, convert_to_pdf, find_libreoffice, validate_converter_exists

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "convert_to_pdf",
    "find_libreoffice",
    "validate_converter_exists",
]
