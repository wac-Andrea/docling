"""Full process over a folder of PDF, Word and plain text documents.

  1. detector.py checks what type each file is, by its content and not only
     by its extension. Renamed or corrupted files are skipped.
  2. converters/file2pdf.py converts whatever is not a PDF (.doc, .docx,
     .rtf, .txt) to a digital PDF. Converted PDFs are saved in <output>/pdf/.
  3. detector.py checks the text layer of every PDF, original and converted.
     Scanned or unreadable ones are skipped: extracting them would take
     minutes to return an empty file. Those with only some pages without
     text are processed, with a warning.
  4. pdf_processor extracts from each PDF the text with its hierarchy, the
     images (saved as PNG) and the tables, each one with its reference in
     the PDF (extractors/).

Everything that happens goes to the log, with the file name: ERROR for the
documents that are not processed, WARNING for those processed but losing
something (pages without text, no sections, no title). By default the log
goes to the console; with --log it is also saved to a file.

Modules do not log anything: as in WAC_DataLib, they raise exceptions
(errors.py) and this command writes them to the log. Another program using
them, such as the RAG project, can do the same with its own log.

Usage:
    python -m document_processor docs-pruebas
    python -m document_processor docs-pruebas -o results --pages 1-10
    python -m document_processor docs-pruebas --log logs/process.log
    python -m document_processor docs-pruebas/acta.docx
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .converters import convert_to_pdf, validate_converter_exists
from .detector import get_file_type, get_output_names, list_documents, validate_vectorial_pdf
from .errors import ConfigurationError, DocumentProcessorError
from .processors.base import collect_blocks, format_outline, has_sections
from .processors.pdf_processor import (
    ALL_PAGES,
    BACKENDS,
    create_document_converter,
    parse_page_range,
    process_pdf,
)

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# Folder, inside the output folder, where the converted PDFs go.
PDF_FOLDER_NAME = "pdf"

# Same format as Factoria's logger (infrastructure/logger.py), the project
# that uses DataLib.
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("document_processor")


def setup_logging(log_file: Path | None) -> None:
    """Log to the console and, if 'log_file' is given, also to it (appending).

    Only this project's logger is configured, not the root one: that way
    docling's internal messages, which are many, do not get mixed in.
    """
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def print_details(result: dict) -> None:
    """Outline, images and tables of a document, when a single one is processed.

    It is the output requested on the console, not a record of the process:
    that is why it goes through print and not to the log.
    """
    print("\n--- Document structure ---")
    print(format_outline(result["tree"]) or "(no headings detected in the document)")

    for block_type, heading in (("picture", "Images"), ("table", "Tables")):
        blocks = collect_blocks(result["tree"], block_type)
        if not blocks:
            continue
        print(f"\n--- {heading}: {len(blocks)} ---")
        for b in blocks:
            page = f"p.{b['page']:<4}" if b["page"] else "      "
            pos = b.get("position")
            size = f"{pos['width']}x{pos['height']} pt" if pos else ""
            reference = b.get("filename") or b["url"] or ""
            class_name = f"{b['class_name']} ({b['confidence']:.0%})" if b.get("class_name") else ""
            print(f"  {b['number']:>3}. {page} {size:<16} {class_name:<26} {reference}")

    print(f"\nGenerated:\n  {result['md']}\n  {result['json']}")


def log_result_warnings(result: dict) -> int:
    """Log what was lost when extracting; return how many warnings.

    The text is fully extracted in all three cases, but the .json is worse:
      - no text: the PDF had a text layer, but nothing came out of it.
      - no sections: the layout model saw no heading, and everything hangs
        from the root. In a converted Word, styled headings also arrive as
        PDF bookmarks; those faked with bold or font size by hand depend on
        the model alone.
      - no title: 'document' is null (see text.get_document_title).
    """
    name = result["filepath"].name
    if result["empty"]:
        logger.warning(f"{name}: no text was extracted")
        return 1
    warnings = 0
    if not has_sections(result["tree"]):
        logger.warning(f"{name}: no headings detected; in the .json everything hangs from the root")
        warnings += 1
    if result["tree"]["document"] is None:
        logger.warning(f"{name}: document title not recognized; 'document' is null")
        warnings += 1
    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m document_processor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="Folder with the documents to process, or a single document")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output folder (default: document_processor/output)")
    parser.add_argument("--pages", type=parse_page_range, default=ALL_PAGES, help="Page range to extract, e.g. 1-10 (for a Word or .txt, of the converted PDF)")
    parser.add_argument("--backend", choices=list(BACKENDS), default="pypdfium2", help="Reader of the PDF text layer")
    parser.add_argument("--images-scale", type=float, default=2.0, help="Resolution of the saved images")
    parser.add_argument("--log", type=Path, help="Also save the log to this file (appending)")
    args = parser.parse_args()

    setup_logging(args.log)

    if not args.path.exists():
        raise SystemExit(f"Not found: {args.path}")
    candidates = list_documents(args.path)
    if not candidates:
        raise SystemExit(f"No PDF, .docx, .doc, .rtf or .txt documents in {args.path}")

    args.output.mkdir(parents=True, exist_ok=True)
    errors = 0  # documents that are not processed

    # --- Step 1: type of each document ------------------------------------
    logger.info(f"[1/4] Checking {len(candidates)} documents in {args.path}")
    file_types: dict[Path, str] = {}
    for p in candidates:
        try:
            file_types[p] = get_file_type(p)
        except DocumentProcessorError as e:
            logger.error(f"Skipping {p.name}: {e}")
            errors += 1
    valid = [p for p in candidates if p in file_types]
    output_names = get_output_names(valid)
    counts = {t: sum(1 for p in valid if file_types[p] == t) for t in ("pdf", "word", "text")}
    logger.info(f"{counts['pdf']} PDF, {counts['word']} Word, {counts['text']} text")

    # --- Step 2: conversion to PDF ----------------------------------------
    # PDF read for each document: its own, or its conversion.
    pdf_paths = {p: p for p in valid if file_types[p] == "pdf"}
    to_convert = [p for p in valid if file_types[p] != "pdf"]
    if to_convert:
        pdf_folder = args.output / PDF_FOLDER_NAME
        logger.info(f"[2/4] Converting {len(to_convert)} documents to PDF in {pdf_folder}")
        try:
            validate_converter_exists()
        except ConfigurationError as e:
            # Without a converter all of them would fail the same way: a
            # single error, not one per file.
            logger.error(f"Skipping {len(to_convert)} documents that are not PDF: {e}")
            errors += len(to_convert)
            to_convert = []
        for p in to_convert:
            start = time.perf_counter()
            try:
                pdf_paths[p] = convert_to_pdf(p, pdf_folder / f"{output_names[p]}.pdf")
            except DocumentProcessorError as e:
                logger.error(f"Skipping {p.name}: {e}")
                errors += 1
                continue
            logger.info(f"{p.name} converted to PDF in {time.perf_counter() - start:.1f}s")
    else:
        logger.info("[2/4] Everything is PDF: nothing to convert")

    # --- Step 3: text layer of the PDFs -----------------------------------
    warnings = 0
    max_pages = args.pages[1] if args.pages[1] < ALL_PAGES[1] else None
    logger.info(f"[3/4] Checking the text layer of {len(pdf_paths)} PDFs")
    extractable = []
    for original, pdf in sorted(pdf_paths.items()):
        try:
            row = validate_vectorial_pdf(pdf, max_pages)
        except DocumentProcessorError as e:
            logger.error(f"Skipping {original.name}: {e}")
            errors += 1
            continue
        if row["status"] == "EMPTY PAGES":
            # The danger of a mixed PDF is that the rest is extracted fine, so
            # without this warning nothing would give away the lost pages.
            pages = ",".join(str(n) for n in row["empty_page_numbers"])
            logger.warning(
                f"{original.name}: {row['empty_pages']}/{row['pages']} pages without text "
                f"(p. {pages}); their content is an image and is lost"
            )
            warnings += 1
        extractable.append(original)

    # --- Step 4: extraction -----------------------------------------------
    logger.info(f"[4/4] Extracting {len(extractable)} documents")

    # A single docling converter: loading the models takes several seconds.
    doc_converter = create_document_converter(args.backend, args.images_scale) if extractable else None

    results = []
    for n, filepath in enumerate(extractable, start=1):
        start = time.perf_counter()
        converted = pdf_paths[filepath] != filepath
        try:
            result = process_pdf(
                pdf_paths[filepath],
                args.output,
                doc_converter,
                args.pages,
                output_names[filepath],
                original_filepath=filepath if converted else None,
            )
        except DocumentProcessorError as e:
            logger.error(f"[{n}/{len(extractable)}] {e}")
            errors += 1
            continue
        results.append(result)
        source = f", converted from {filepath.suffix.lower()}" if converted else ""
        logger.info(
            f"[{n}/{len(extractable)}] {filepath.name}: {result['pages']} pages, {len(result['images'])} images, "
            f"{len(collect_blocks(result['tree'], 'table'))} tables, {time.perf_counter() - start:.1f}s{source}"
        )
        warnings += log_result_warnings(result)

    if len(candidates) == 1 and results:
        print_details(results[0])

    # --- Summary -----------------------------------------------------------
    images = sum(len(r["images"]) for r in results)
    tables = sum(len(collect_blocks(r["tree"], "table")) for r in results)
    logger.info(
        f"Done: {len(results)}/{len(candidates)} documents processed, {images} images, "
        f"{tables} tables; {errors} not processed, {warnings} warnings. Output: {args.output}"
    )


if __name__ == "__main__":
    main()
