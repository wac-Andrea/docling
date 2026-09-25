"""Digital PDF -> DoclingDocument, with the native text layer and no OCR.

Meant for PDFs with a text layer: without OCR it is fast and the text comes
out exactly as the PDF generator wrote it. A scanned PDF has no such layer
and would come out empty; the detector rejects it beforehand
(validate_vectorial_pdf).
"""

from __future__ import annotations

from pathlib import Path

from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling.backend.pypdfium2_backend import (
    PyPdfiumDocumentBackend,
    PyPdfiumPageBackend,
    pypdfium2_lock,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import HeadingHierarchyOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

from ..detector import MIN_CELL_HEIGHT
from ..errors import ExtractionError
from .base import save_results


class _CleanPyPdfiumPageBackend(PyPdfiumPageBackend):
    """pypdfium2 page without the degenerate rectangles (slivers)."""

    def _compute_text_cells(self):
        cells = super()._compute_text_cells()
        return [c for c in cells if abs(c.rect.to_bounding_box().height) >= MIN_CELL_HEIGHT]


class CleanPyPdfiumDocumentBackend(PyPdfiumDocumentBackend):
    """pypdfium2 with the sliver filter applied to each page."""

    def load_page(self, page_no: int) -> PyPdfiumPageBackend:
        with pypdfium2_lock:
            return _CleanPyPdfiumPageBackend(self._pdoc, self.document_hash, page_no)


# Which reader parses the PDF text layer. Not a minor detail: docling's
# default backend ('docling-parse') drops characters in some PDFs -- stray
# accents, letters of headings -- that pypdfium2 does read.
BACKENDS = {
    "pypdfium2": CleanPyPdfiumDocumentBackend,
    "pypdfium2-raw": PyPdfiumDocumentBackend,
    "docling-parse": DoclingParseV4DocumentBackend,
}

ALL_PAGES = (1, 2**31)


def create_document_converter(backend: str = "pypdfium2", images_scale: float = 2.0) -> DocumentConverter:
    """docling converter for PDF, with OCR disabled.

    docling also crops the region of each figure to save it as PNG. It crops
    the detected area, not the embedded bitmap: that way diagrams made of
    vectors come out too, which an embedded-image extraction does not see.

    Each figure goes through DocumentFigureClassifier, which labels it (logo,
    photograph, bar_chart...): see extractors.images.get_classification.

    The layout model only marks that something is a heading, not its level:
    on its own, every heading of a PDF comes out at the same level. The
    heading hierarchy stage assigns it from three signals, by priority: PDF
    bookmarks (its navigable outline), numbering (8. > 8.1 > 8.1.1 > a.) and
    style. It works best with numbered headings; mixing numbered and
    unnumbered ones it may hang a section from what is really its sibling. It
    only changes levels: the text of each section does not vary, only the
    path of parent headings.

    Style needs the parsed pages (generate_parsed_pages). With the pypdfium2
    reader there is no font name, so only the font size counts there, not
    bold or italics.
    """
    options = PdfPipelineOptions(do_ocr=False)
    options.generate_picture_images = True
    options.images_scale = images_scale
    options.do_picture_classification = True
    options.heading_hierarchy_options = HeadingHierarchyOptions(enabled=True)
    options.generate_parsed_pages = True
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=options,
                backend=BACKENDS[backend],
            )
        }
    )


def parse_page_range(value: str) -> tuple[int, int]:
    """Turn '3' or '1-20' into the range docling expects."""
    if "-" in value:
        start, end = value.split("-", 1)
        return int(start), int(end)
    return int(value), int(value)


def process_pdf(
    filepath: Path,
    output_dir: Path,
    doc_converter: DocumentConverter,
    page_range: tuple[int, int] = ALL_PAGES,
    output_name: str | None = None,
    original_filepath: Path | None = None,
) -> dict:
    """Extract a PDF and write its .md, its .json and its images to 'output_dir'.

    It takes the docling converter already built instead of creating it:
    loading the layout models takes several seconds, and when processing a
    folder it pays to do it only once for all the documents.

    'original_filepath' is the file the user gave when 'filepath' is its
    conversion (converters.file2pdf): the 'title' of the tree comes from it.
    The URLs still point to 'filepath', which is the one with pages.

    Raises
    ------
    ExtractionError
        If docling cannot convert the PDF or writing the outputs fails.
    """
    source = original_filepath or filepath
    try:
        doc = doc_converter.convert(filepath, page_range=page_range).document
        if original_filepath is not None:
            doc.name = original_filepath.stem
        result = save_results(doc, filepath, output_dir, output_name)
    except Exception as e:
        raise ExtractionError(f"Could not extract {source.name}: {type(e).__name__}: {e}", source, e) from e
    result["filepath"] = source
    result["pages"] = len(doc.pages)
    return result
