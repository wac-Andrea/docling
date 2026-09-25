# docling

Extraction of text with its hierarchy (headings, lists, tables and images) from
PDF, Word and plain text documents, based on [docling](https://github.com/docling-project/docling).
No OCR: only the native text layer of the documents is processed.

## Usage

Everything runs from the project root with `python -m document_processor`,
passing it a folder (subfolders are walked too) or a single document. It
accepts `.pdf`, `.docx`, `.doc`, `.rtf` and `.txt` mixed together:

```bash
python -m document_processor docs-pruebas
python -m document_processor docs-pruebas -o results
python -m document_processor docs-pruebas/acta.docx
```

With a single document it also prints its outline, images and tables to the
console.

The process has four steps:

1. **Check**: the real type of each file by its content, not only by its
   extension. Renamed or corrupted files are skipped.
2. **Conversion to PDF**: whatever is not a PDF (`.doc`, `.docx`, `.rtf`,
   `.txt`) is converted to a digital PDF with `converters/file2pdf.py`. docling
   gets a better structure out of a PDF than out of a Word, so every document
   follows the same path.
3. **Text layer** of every PDF, original and converted: scanned or unreadable
   ones are skipped; those with some pages without text are processed with a
   warning.
4. **Extraction** of each PDF.

For each document it writes to the output folder (by default
`document_processor/output/`, ignored by git):

- `<name>.md`: the text as Markdown.
- `<name>.json`: the section tree with texts, lists, tables and images.
- `images/<name>/`: each image as PNG.
- `pdf/<name>.pdf`: the converted PDF, if the original was not a PDF.

The root of the `.json` holds in `document` the title of the document, or
`null` if it is not recognized: the first heading, if it is on the first page.
`title` is the name of the original file.

Each node of the tree has `title`, `level`, `page`, `content` (its blocks) and
`subsections`. Each block has a `type` (`text`, `list_item`, `table`,
`picture`, `caption`...), its `page` and its `text`; list items also have
`level` and `marker`. Each image and each table carries its `number` in the
document, its `url` (opens the PDF at that page) and its `position`; images
also carry `filename` and `image_url` with the path to their PNG. In a
converted document, `page` and `url` refer to the PDF in `pdf/`.

Section levels are assigned by docling's heading hierarchy stage, from the
PDF bookmarks, the numbering (`8.` › `8.1` › `8.1.1` › `a.`) and the font
size. It works best with numbered headings; when numbered and unnumbered ones
are mixed, it may hang a section from what is really its sibling. When a Word
is converted, its styled headings (Heading 1, Heading 2…) become PDF bookmarks.

Each image also carries `class_name` and `confidence` (0-1), from docling's
`DocumentFigureClassifier` model: `logo`, `icon`, `photograph`, `bar_chart`,
`flow_chart`, `engineering_drawing`… (26 classes). Useful to drop logos and
layout decorations before indexing into a RAG. With low confidence the class
is doubtful: better not to filter on it alone.

If a PDF and a Word have the same name (`informe.pdf` and `informe.docx`),
their outputs are told apart by the extension: `informe-pdf.json` and
`informe-docx.json`.

Options: `--pages 1-10` to limit the range (for a Word or `.txt`, of the
converted PDF), `--backend` to change the text layer reader, `--images-scale`
for the resolution of the PNGs and `--log file.log` to also save the log to a
file.

The text layer check and the conversion to PDF can also be run on their own,
for example to review a corpus before processing it:

```bash
python -m document_processor.detector docs-pruebas --pages 10
python -m document_processor.converters docs-pruebas/acta.docx -o pdfs
```

## Log and errors

Everything that happens in the process goes to the log, with the file name:

```
2026-09-25 12:28:11 | ERROR | Skipping renombrado.pdf: The content of renombrado.pdf is not a valid .pdf
2026-09-25 12:28:27 | ERROR | Skipping escaneado.pdf: escaneado.pdf has no text layer (scanned): it would come out empty
2026-09-25 12:28:27 | WARNING | mixto.pdf: 1/2 pages without text (p. 2); their content is an image and is lost
2026-09-25 12:28:45 | WARNING | nota-ansi.txt: document title not recognized; 'document' is null
```

- `ERROR`: the document is not processed (invalid format, failed conversion,
  scanned or unreadable PDF, extraction failure).
- `WARNING`: it is processed, but loses something (pages without text, no
  headings, no title).
- `INFO`: progress and the final summary.

By default the log goes to the console; with `--log` it is also saved to a
file. Only the `document_processor` logger is configured, not the root one, so
docling's internal messages do not get mixed in.

Modules do not write to the log: as in
[WAC_DataLib](https://github.com/WeAreClickers/WAC_DataLib), they raise
exceptions and the caller decides what to do with them. They are in
`errors.py`, with the same names as in DataLib:

| Exception | When |
|---|---|
| `DocumentProcessorError` | Base of all of them. |
| `UnsupportedFormatError` | Unsupported extension, or content that does not match its extension. |
| `ConfigurationError` | Neither LibreOffice nor Word to convert with. |
| `ConversionError` | The conversion to PDF fails. Carries `filepath` and `original_error`. |
| `ExtractionError` | Extraction fails or the PDF cannot be opened. Carries `filepath` and `original_error`. |
| `NotVectorialError` | Subclass of `ExtractionError`: scanned PDF, without a text layer. |

From other code, for example the RAG project, they are used like this:

```python
from document_processor.converters import convert_to_pdf
from document_processor.detector import validate_vectorial_pdf
from document_processor.errors import DocumentProcessorError, NotVectorialError

try:
    pdf = convert_to_pdf("informe.docx", "output/informe.pdf")
    validate_vectorial_pdf(pdf)
except NotVectorialError as e:
    ...  # scanned: e.filepath
except DocumentProcessorError as e:
    ...  # any other failure
```

## Naming

Names follow [WAC_DataLib](https://github.com/WeAreClickers/WAC_DataLib) so
both projects read alike:

| Here | DataLib equivalent |
|---|---|
| `errors.py`: `ConversionError`, `ExtractionError`, `UnsupportedFormatError`, `ConfigurationError` | `file2text/errors.py`, same names |
| `converters/file2pdf.py`: `convert_to_pdf(filepath, save_path)` | `file2text/convert_file_format.py`: `convert_file_format(filepath)` |
| `validate_converter_exists()` | `file2text/validation.py`: `validate_binary_exists()` |
| `LIBRE_OFFICE_CMD`, `SUPPORTED_EXTENSIONS` | `utils/constants.py`: `LIBRE_OFFICE_CMD`; `BaseConverter.SUPPORTED_EXTENSIONS` |
| `detector.py`: `validate_vectorial_pdf()`, `NotVectorialError`, `MIN_CHARS = 50` | `utils/file.py`: `is_vectorial_pdf(filepath, min_chars=50)` |
| `get_file_type()` | `extract_text.py`: `get_extractor_for()` |
| Parameters `filepath`, `save_path`; images `image{page}_{n}.png` | Same |

## Structure

```
document_processor/
    __main__.py            main command: checks, converts, validates and extracts; writes the log
    detector.py            real type of each file, output names and text layer of the PDFs
    errors.py              exceptions, following WAC_DataLib's approach

    converters/            turn into PDF whatever is not, before extracting
        file2pdf.py        .doc, .docx, .rtf and .txt to digital PDF (LibreOffice or Word)

    processors/            obtain the DoclingDocument
        base.py            the common part: section tree and writing of .md, .json and images
        pdf_processor.py   native text layer, sliver filter, heading levels, page range

    extractors/            one per kind of item: they turn it into a block of the .json
        text.py            headings, paragraphs (and split paragraphs) and lists with level and marker
        images.py          image blocks, their class (logo, photo...) and PNG saving
        tables.py          table blocks, without duplicating the text of their cells
        links.py           URLs and location: page, #page=N, position, paths to PNGs

    output/                default output: <document>.json, .md, images/, pdf/
```

The extractors and `processors/base.py` work on docling's internal model
(`DoclingDocument`), not on the PDF: a format docling could read directly
would only need its own processor.

## Requirements

### Python

- Python 3.10 or higher (tested with 3.13).
- Packages:

  ```bash
  pip install docling
  ```

  `docling` already includes `pypdfium2`, which the scripts use.

### docling models

The models (PDF layout and tables, image classifier) are downloaded from
HuggingFace the first time they are used and stay cached. On a server or in
Docker it is better to download them when building the image, so as not to
depend on the internet while processing:

```dockerfile
RUN docling-tools models download layout tableformer picture_classifier
```

### Conversion to PDF (`.doc`, `.docx`, `.rtf`, `.txt`)

`converters/file2pdf.py` converts to PDF whatever is not a PDF, and for that
**one** of these two programs is needed on the machine running the script:

| Program | Where it is used | Notes |
|---|---|---|
| **LibreOffice** | Server / Docker (recommended) | Used whenever it is installed. |
| **Microsoft Word** | Windows only, local development | Used if there is no LibreOffice. Also needs `pip install pywin32`. |

Both generate a digital PDF, with a text layer and with bookmarks taken from
the document headings. A `.txt` is turned into UTF-8 first: its encoding
(UTF-8, UTF-16 or Windows-1252) is detected, because each program guesses it
its own way and Word, for example, breaks the accents of a Windows-1252 `.txt`.

Without either of them, PDFs are processed normally and the `.doc`, `.docx`,
`.rtf` and `.txt` files are skipped, with a single `ERROR` in the log
(`ConfigurationError`: `Cannot convert to PDF: install LibreOffice or
Microsoft Word`).

#### Installing LibreOffice

- **Docker** (Debian/Ubuntu based image, e.g. `python:3.13-slim`):

  ```dockerfile
  RUN apt-get update \
   && apt-get install -y --no-install-recommends libreoffice-writer \
   && rm -rf /var/lib/apt/lists/*
  ```

  `libreoffice-writer` is enough; the full suite is not needed.

- **Linux**: `sudo apt-get install -y libreoffice-writer`
- **Mac**: `brew install --cask libreoffice`
- **Windows**: installer from [libreoffice.org](https://www.libreoffice.org/download/).
  There is no need to add it to the PATH: the script also looks for it in
  `C:\Program Files\LibreOffice`.

> **Parallel conversions.** LibreOffice does not support two conversions at
> the same time with the same user profile. Today documents are converted one
> by one, so there is no problem; if the service ends up handling several
> requests in parallel, each conversion will need its own profile
> (`-env:UserInstallation=file:///tmp/lo-<id>`).
