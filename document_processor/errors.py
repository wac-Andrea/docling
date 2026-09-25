"""Document processor exceptions.

Same approach as WAC_DataLib (wac/core/file2text/errors.py): one base
exception per package and one subclass per kind of failure; those tied to a
file keep its path ('filepath') and the originating exception
('original_error'). Modules do not log anything: they raise the exception,
and the caller decides what to do with it (__main__ writes it to the log).

Names match DataLib so that, once this is mounted in a project that also
uses it, errors are caught the same way.
"""

from __future__ import annotations

from pathlib import Path


class DocumentProcessorError(Exception):
    """Base exception for all document processor errors."""


class UnsupportedFormatError(DocumentProcessorError):
    """File format is not supported for processing.

    Either by extension (not .pdf, .doc, .docx, .rtf or .txt) or by content
    (the extension does not match what is inside: renamed or corrupted).
    """


class ConfigurationError(DocumentProcessorError):
    """Missing requirement on the machine (e.g., neither LibreOffice nor Word to convert)."""


class ConversionError(DocumentProcessorError):
    """Error converting a document to PDF."""

    def __init__(self, message: str, filepath: str | Path, original_error: Exception | None = None) -> None:
        """
        Initialize conversion error.

        Parameters
        ----------
        message : str
            Error description.
        filepath : str or Path
            Path to the file that caused the error.
        original_error : Exception or None
            Original exception that was caught, if any.
        """
        self.filepath = filepath
        self.original_error = original_error
        super().__init__(message)


class ExtractionError(DocumentProcessorError):
    """Error during content extraction from a document."""

    def __init__(self, message: str, filepath: str | Path, original_error: Exception | None = None) -> None:
        """
        Initialize extraction error.

        Parameters
        ----------
        message : str
            Error description.
        filepath : str or Path
            Path to the file that caused the error.
        original_error : Exception or None
            Original exception that was caught, if any.
        """
        self.filepath = filepath
        self.original_error = original_error
        super().__init__(message)


class NotVectorialError(ExtractionError):
    """PDF without a text layer (scanned): extracting it would give an empty result.

    'Vectorial' is DataLib's term (utils/file.py: is_vectorial_pdf).
    """
