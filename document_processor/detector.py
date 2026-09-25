"""What each file is and whether it can be processed, before extracting anything.

  - Real type of each file (PDF, Word or plain text) by its content, not only
    by its extension: renamed or corrupted files are skipped before wasting
    time on them. Whatever is not a PDF is then converted to PDF
    (converters.file2pdf).
  - Output names without clashes when two documents have the same name.
  - Audit of the PDF text layer: flags those that cannot be extracted
    (scanned, unreadable) so they are not processed blindly.

As in WAC_DataLib, what cannot be processed is reported with an exception
(see errors.py), not with a report: the caller decides whether to log it.

The audit can also be run on its own, without extracting, to review a corpus:
    python -m document_processor.detector folder/ --pages 10
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

from .errors import ExtractionError, NotVectorialError, UnsupportedFormatError

# First bytes each format must have. Anyone can change the extension; the
# content does not lie. A .doc may actually be an RTF -- Word sometimes saves
# it that way --, and the converter opens it all the same.
SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0", b"{\\rtf"),
    ".rtf": (b"{\\rtf",),
}

# Extensions that are processed. A .txt has no signature: see get_file_type.
SUPPORTED_EXTENSIONS = {*SIGNATURES, ".txt"}


def detect_text_encoding(filepath: Path) -> str | None:
    """Encoding of a plain text file, or None if it is not text.

    UTF-8 is tried first, the usual one today, and if it does not fit,
    Windows-1252, what old programs on Windows tend to leave. A file with
    null bytes and no UTF-16 mark is binary, not text.
    """
    data = filepath.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        data.decode("cp1252")
        return "cp1252"
    except UnicodeDecodeError:  # bytes not even cp1252 defines: not text
        return None


def get_file_type(filepath: Path) -> str:
    """'pdf', 'word' or 'text', if the content matches the extension.

    Detects renamed or corrupted files before wasting time on them. A
    password-protected .docx does not pass either: it is encrypted inside a
    container that is not a .docx one, and it could not be converted.

    Raises
    ------
    UnsupportedFormatError
        If the extension is not processed or the content does not match it.
    """
    ext = filepath.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported file format: {ext or '(no extension)'}. "
            f"Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if ext == ".txt":
        # A .txt has no signature: it is enough that it can be read as text.
        valid = detect_text_encoding(filepath) is not None
    else:
        with filepath.open("rb") as f:
            header = f.read(1024)
        if ext == ".pdf":
            # The standard allows some garbage before '%PDF' in the first KB.
            valid = b"%PDF" in header
        else:
            valid = header.startswith(SIGNATURES[ext])
    if not valid:
        raise UnsupportedFormatError(f"The content of {filepath.name} is not a valid {ext}")
    if ext == ".pdf":
        return "pdf"
    return "text" if ext == ".txt" else "word"


def list_documents(path: Path) -> list[Path]:
    """Documents of a folder (with subfolders), or the single file.

    The '~$...' files Word leaves while a document is open are skipped: they
    have a .docx extension but are lock files, not documents.
    """
    if path.is_file():
        return [path]
    return sorted(
        p for p in path.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith("~$")
    )


def get_output_names(filepaths: list[Path]) -> dict[Path, str]:
    """Name of the output files of each document, without clashes.

    If the folder holds 'informe.pdf' and 'informe.docx' -- a Word and the PDF
    exported from it, a common case --, both would write 'informe.json'. In
    that case the extension is added: 'informe-pdf.json', 'informe-docx.json'.
    Comparison ignores case because on Windows 'A.json' and 'a.json' are the
    same file.
    """
    repetitions = Counter(p.stem.lower() for p in filepaths)
    names: dict[Path, str] = {}
    used: set[str] = set()
    for p in filepaths:
        base = p.stem if repetitions[p.stem.lower()] == 1 else f"{p.stem}-{p.suffix.lstrip('.').lower()}"
        name, n = base, 2
        while name.lower() in used:  # same name and extension in another subfolder
            name, n = f"{base}-{n}", n + 1
        used.add(name.lower())
        names[p] = name
    return names


# --- Audit of the PDF text layer --------------------------------------------

AUDIT_HELP = """Audit a folder of PDFs before processing them, without opening them by hand.

Meant for large corpora that cannot be reviewed one by one: flags the
documents that will cause trouble so you only look at those.

Possible statuses:
  - NO TEXT     : no page has a text layer (fully scanned).
                  Extraction would return it empty, silently.
  - EMPTY PAGES : mixed PDF: some pages are images. The rest is extracted
                  fine, so nothing gives away what is missing.
  - UNREADABLE  : the file cannot be opened.
  - OK          : every page has text.

It also counts the "slivers": degenerate rectangles that duplicate a
character. The PDF processor already filters them out when extracting; here
they are only reported.
"""

# Minimum height, in points, for a PDF text cell to be considered real. Used
# by the audit (to count slivers) and by the PDF processor (to filter them
# out when extracting).
#
# pypdfium2 now and then emits degenerate rectangles: slivers of almost zero
# height that hold no letter of their own, but overlap the neighbouring
# glyph. docling asks them for their text and ends up duplicating that
# character (a heading '... JIT Y LEAN' comes out as 'LEAN N').
#
# The threshold is measured, not eyeballed. Over 477 pages of real documents:
# slivers = 0.014 pt, and the flattest legitimate glyph (a hyphen) = 0.587 pt.
# 0.1 leaves a factor of 6 of margin on both sides. Raising it up to 1.0 would
# start deleting hyphens, dashes and minus signs.
MIN_CELL_HEIGHT = 0.1

# Characters per page below which the page is considered to have no text
# (same threshold as WAC_DataLib's min_chars).
MIN_CHARS = 50


def audit_pdf(filepath: Path, max_pages: int | None = None) -> dict:
    """Review a PDF and return its metrics and its verdict.

    'max_pages' limits the review to the first N pages.
    """
    row = {
        "file": filepath.name,
        "pages": 0,
        "chars": 0,
        "slivers": 0,
        "empty_pages": 0,
        "status": "",
        "detail": "",
        "error": None,  # the exception, if it could not be opened
        "empty_page_numbers": [],
    }
    try:
        pdf = pdfium.PdfDocument(filepath)
    except Exception as e:
        row["status"] = "UNREADABLE"
        row["detail"] = type(e).__name__
        row["error"] = e
        return row

    samples: list[str] = []
    empty: list[int] = []
    total = len(pdf) if max_pages is None else min(len(pdf), max_pages)
    for n_page in range(total):
        try:
            textpage = pdf[n_page].get_textpage()
        except Exception:
            continue
        row["pages"] += 1
        page_chars = textpage.count_chars()
        row["chars"] += page_chars
        # Idea taken from WAC_DataLib (_is_vectorial_page): a page with fewer
        # than a few dozen characters has no useful text, it is scanned.
        if page_chars < MIN_CHARS:
            empty.append(n_page + 1)
        for i in range(textpage.count_rects()):
            x0, y0, x1, y1 = textpage.get_rect(i)
            if (y1 - y0) >= MIN_CELL_HEIGHT:
                continue
            text = textpage.get_text_bounded(x0, y0, x1, y1)
            if text.strip():  # only those duplicating a real character count
                row["slivers"] += 1
                if len(samples) < 3:
                    samples.append(f"p.{n_page + 1}:{text.strip()[:6]!r}")

    row["empty_pages"] = len(empty)
    row["empty_page_numbers"] = empty
    if row["chars"] == 0:
        row["status"] = "NO TEXT"
        row["detail"] = "scanned: would come out empty"
    elif empty:
        # The danger of a mixed PDF is that the rest is extracted fine, so
        # nothing gives away that those pages have been lost.
        listing = ",".join(str(p) for p in empty[:6])
        if len(empty) > 6:
            listing += f",+{len(empty) - 6}"
        row["status"] = "EMPTY PAGES"
        row["detail"] = f"{len(empty)}/{row['pages']} without text: p.{listing}"
    else:
        row["status"] = "OK"
        row["detail"] = " ".join(samples)
    return row


def validate_vectorial_pdf(filepath: Path, max_pages: int | None = None) -> dict:
    """Check that a PDF can be extracted and return its audit.

    Plays the role of DataLib's is_vectorial_pdf (utils/file.py), with two
    differences: it tells a fully scanned PDF apart from one with only some
    scanned pages, and instead of returning True/False it raises the
    exception for the case. A PDF with EMPTY PAGES can be extracted: its
    audit is returned so the caller can warn about the pages that are lost.

    Raises
    ------
    ExtractionError
        If the PDF cannot be opened (UNREADABLE).
    NotVectorialError
        If no page has a text layer (NO TEXT): scanned.
    """
    row = audit_pdf(filepath, max_pages)
    if row["status"] == "UNREADABLE":
        raise ExtractionError(f"Cannot open {filepath.name}: {row['detail']}", filepath, row["error"])
    if row["status"] == "NO TEXT":
        raise NotVectorialError(f"{filepath.name} has no text layer (scanned): it would come out empty", filepath)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=AUDIT_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="Folder with the PDFs to audit")
    parser.add_argument("--pages", type=int, default=None, help="Review only the first N pages of each PDF")
    args = parser.parse_args()

    pdfs = sorted(args.folder.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No PDFs in {args.folder}")

    limit = f" (first {args.pages} pages)" if args.pages else ""
    print(f"Auditing {len(pdfs)} PDFs in {args.folder}{limit}...\n")
    rows = [audit_pdf(p, args.pages) for p in pdfs]

    problematic = [r for r in rows if r["status"] != "OK"]
    with_slivers = [r for r in rows if r["status"] == "OK" and r["slivers"]]

    if problematic:
        print("REVIEW (will not be processed properly):")
        for r in problematic:
            print(f"  {r['status']:11s} {r['detail']:30s} {r['file']}")
    if with_slivers:
        print("\nWith slivers (already fixed by the filter, informative only):")
        for r in with_slivers:
            print(f"  {r['slivers']:3d}  {r['detail']:30s} {r['file']}")

    pages = sum(r["pages"] for r in rows)
    slivers = sum(r["slivers"] for r in rows)
    print(f"\nSummary: {len(rows)} PDFs, {pages} pages")
    print(f"  correct   : {len(rows) - len(problematic)}")
    print(f"  to review : {len(problematic)}")
    print(f"  slivers   : {slivers} (filtered when extracting)")


if __name__ == "__main__":
    main()
