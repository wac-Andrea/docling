"""Word (.doc, .docx, .rtf) and plain text (.txt) -> digital PDF.

docling gets a worse structure out of a Word than out of a PDF, so whatever
is not a PDF is converted to PDF first and follows the same path as the
rest: same text layer check, same processor and '#page=N' URLs pointing to
the converted PDF.

The PDF comes out digital, with a text layer, because the office program
itself generates it when exporting, not an image print. It also carries
bookmarks created from the headings, which docling's heading hierarchy stage
uses to assign the section levels.

To convert, it first looks for LibreOffice, as WAC_DataLib does
(file2text/convert_file_format.py), and if it is not installed it uses
Microsoft Word through COM automation (Windows only). Failures are raised as
in DataLib: ConfigurationError if there is nothing to convert with,
UnsupportedFormatError if the format is not converted and ConversionError if
the conversion fails.

Standalone usage:
    python -m document_processor.converters docs-pruebas/acta.docx -o pdfs/
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..detector import detect_text_encoding
from ..errors import ConfigurationError, ConversionError, DocumentProcessorError, UnsupportedFormatError

# Formats converted to PDF before extracting.
SUPPORTED_EXTENSIONS = (".doc", ".docx", ".rtf", ".txt")

# External tool, same name as in WAC_DataLib (wac/utils/constants.py). Some
# Linux distributions only install it as 'libreoffice'.
LIBRE_OFFICE_CMD = "soffice"
LIBRE_OFFICE_CMDS = (LIBRE_OFFICE_CMD, "libreoffice")

# A document that takes longer to convert is usually stuck on a dialog
# (protected document, repair...). DataLib uses 30 s to go from .doc to
# .docx; exporting to PDF, laying out every page, takes longer.
CONVERSION_TIMEOUT = 60

NO_CONVERTER_MESSAGE = "Cannot convert to PDF: install LibreOffice or Microsoft Word (with pywin32)"


def find_libreoffice() -> str | None:
    """Path of the LibreOffice executable, or None if it is not installed.

    On Windows the installer does not add it to the PATH, so the default
    installation folders are checked too.
    """
    for cmd in LIBRE_OFFICE_CMDS:
        if path := shutil.which(cmd):
            return path
    for folder in (r"C:\Program Files\LibreOffice", r"C:\Program Files (x86)\LibreOffice"):
        exe = Path(folder) / "program" / "soffice.exe"
        if exe.is_file():
            return str(exe)
    return None


def validate_converter_exists() -> None:
    """Check there is something to convert with, before starting.

    Equivalent to DataLib's validate_binary_exists (file2text/validation.py),
    with Word as the alternative on Windows. Whether Word is really installed
    is only known when opening it: if it is not, the conversion raises
    ConfigurationError too.

    Raises
    ------
    ConfigurationError
        If there is no LibreOffice and, on Windows, no pywin32 to use Word either.
    """
    if find_libreoffice():
        return
    if sys.platform == "win32":
        try:
            import win32com.client  # noqa: F401

            return
        except ImportError:
            pass
    raise ConfigurationError(NO_CONVERTER_MESSAGE)


def _convert_with_libreoffice(soffice: str, filepath: Path, output_folder: Path, original: Path) -> Path:
    """Convert with LibreOffice; leaves '<name>.pdf' in 'output_folder'."""
    command = [soffice, "--headless"]
    if filepath.suffix.lower() == ".txt":
        command.append("--infilter=Text (encoded):UTF8")  # see _utf8_copy
    # writer_pdf_Export exports the heading bookmarks by default.
    command += ["--convert-to", "pdf:writer_pdf_Export", str(filepath), "--outdir", str(output_folder)]
    try:
        subprocess.run(command, check=True, timeout=CONVERSION_TIMEOUT, capture_output=True)
    except subprocess.TimeoutExpired as e:
        raise ConversionError(
            f"LibreOffice conversion timed out after {CONVERSION_TIMEOUT}s for {original.name}", original, e
        ) from e
    except subprocess.CalledProcessError as e:
        detail = e.stderr.decode(errors="replace").strip()
        raise ConversionError(f"LibreOffice conversion failed for {original.name}: {detail}", original, e) from e
    return output_folder / f"{filepath.stem}.pdf"


def _convert_with_word(filepath: Path, output_folder: Path, original: Path) -> Path:
    """Convert with Word through COM; leaves '<name>.pdf' in 'output_folder'."""
    try:
        import pythoncom
        import win32com.client
    except ImportError as e:
        raise ConfigurationError(NO_CONVERTER_MESSAGE) from e

    output_path = output_folder / f"{filepath.stem}.pdf"
    open_options = {}
    if filepath.suffix.lower() == ".txt":
        # Without this Word guesses the encoding, and sometimes asks in a dialog.
        open_options = {"Format": 5, "Encoding": 65001}  # wdOpenFormatEncodedText, UTF-8: see _utf8_copy

    pythoncom.CoInitialize()
    try:
        # DispatchEx opens its own Word instance: if the user has Word open,
        # their session and documents are left untouched.
        word = win32com.client.DispatchEx("Word.Application")
    except Exception as e:
        pythoncom.CoUninitialize()
        raise ConfigurationError(NO_CONVERTER_MESSAGE) from e
    try:
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone
        document = word.Documents.Open(
            str(filepath.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            # With any password, a protected document raises an error instead
            # of opening a dialog that would leave the script stuck. Documents
            # without a password are not affected.
            PasswordDocument="-",
            **open_options,
        )
        try:
            document.ExportAsFixedFormat(
                OutputFileName=str(output_path.resolve()),
                ExportFormat=17,  # wdExportFormatPDF
                OpenAfterExport=False,
                OptimizeFor=0,  # wdExportOptimizeForPrint
                CreateBookmarks=1,  # wdExportCreateHeadingBookmarks: heading bookmarks
            )
        finally:
            document.Close(False)
    except Exception as e:
        raise ConversionError(f"Word conversion failed for {original.name}: {e}", original, e) from e
    finally:
        word.Quit()
        pythoncom.CoUninitialize()
    return output_path


def _utf8_copy(filepath: Path, output_folder: Path) -> Path:
    """UTF-8 (with BOM) copy of a .txt, to convert that one and not the original.

    A .txt does not say which encoding it is in, and each program guesses it
    its own way: Word, even when told Windows-1252, reads the accents of a
    Windows .txt as Chinese characters. Here it is read with the detected
    encoding (detector.detect_text_encoding) and rewritten as UTF-8, which
    both programs read correctly.
    """
    encoding = detect_text_encoding(filepath)
    if encoding is None:
        raise UnsupportedFormatError(f"{filepath.name} is not a text file")
    copy = output_folder / filepath.name
    copy.write_text(filepath.read_text(encoding=encoding), encoding="utf-8-sig")
    return copy


def convert_to_pdf(filepath: str | Path, save_path: str | Path) -> Path:
    """
    Convert a document to a digital PDF.

    It is converted in a temporary folder and then moved: both programs pick
    the PDF name from the original, and this way they overwrite nothing.

    Parameters
    ----------
    filepath : str or Path
        Path to the .doc, .docx, .rtf or .txt document.
    save_path : str or Path
        Path of the PDF to generate; its folder is created if it does not exist.

    Returns
    -------
    Path
        Path to the generated PDF ('save_path').

    Raises
    ------
    UnsupportedFormatError
        If the file extension is not one that is converted.
    ConfigurationError
        If there is neither LibreOffice nor Word to convert with.
    ConversionError
        If the conversion fails, times out or does not generate the PDF.
    """
    filepath = Path(filepath)
    save_path = Path(save_path)
    if filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported format for conversion: {filepath.suffix}. "
            f"Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}"
        )
    validate_converter_exists()
    original = filepath

    with tempfile.TemporaryDirectory() as output_folder, tempfile.TemporaryDirectory() as copies_folder:
        if filepath.suffix.lower() == ".txt":
            filepath = _utf8_copy(filepath, Path(copies_folder))
        if soffice := find_libreoffice():
            generated = _convert_with_libreoffice(soffice, filepath, Path(output_folder), original)
        else:
            generated = _convert_with_word(filepath, Path(output_folder), original)

        if not generated.is_file():
            raise ConversionError(f"Conversion did not generate the expected PDF for {original.name}", original)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(generated, save_path)
    return save_path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m document_processor.converters", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", type=Path, nargs="+", help=".doc, .docx, .rtf or .txt documents")
    parser.add_argument("-o", "--output", type=Path, default=Path("."), help="Folder where the PDFs are saved")
    args = parser.parse_args()

    for filepath in args.files:
        try:
            pdf = convert_to_pdf(filepath, args.output / f"{filepath.stem}.pdf")
        except DocumentProcessorError as e:
            print(f"ERROR {filepath.name}: {type(e).__name__}: {e}")
            continue
        print(f"{filepath.name} -> {pdf}")


if __name__ == "__main__":
    main()
