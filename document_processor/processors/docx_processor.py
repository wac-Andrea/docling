"""Word (.docx, .doc, .rtf) -> DoclingDocument.

La extraccion solo trabaja con .docx. Un .doc (formato binario de Word 97-2003)
o un .rtf se convierte antes a .docx en una carpeta temporal y se procesa igual
que el resto; la copia convertida se borra al terminar.

Para convertir busca primero LibreOffice, como hace WAC_DataLib
(file2text/convert_file_format.py), y si no esta instalado usa Microsoft Word
por automatizacion COM (solo Windows).

Un .docx no tiene paginas -- la paginacion la calcula Word al pintarlo --, asi
que en el .json 'pagina' y 'url' salen siempre a null.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import ConvertPipelineOptions
from docling.document_converter import DocumentConverter, WordFormatOption

from .base import guardar_resultados

# Formatos que hay que pasar a .docx antes de extraer.
A_CONVERTIR = {".doc", ".rtf"}

# Mismo limite que WAC_DataLib: un documento que tarda mas en convertirse
# suele estar colgado en un dialogo (documento protegido, reparacion...).
TIMEOUT_CONVERSION = 30

SIN_CONVERSOR = "no se puede convertir a .docx: instala LibreOffice o Microsoft Word"


class ErrorConversion(Exception):
    """No se pudo convertir un .doc o .rtf a .docx."""


def buscar_libreoffice() -> str | None:
    """Ruta del ejecutable de LibreOffice, o None si no esta instalado.

    En Windows el instalador no lo mete en el PATH, asi que tambien se mira
    en las carpetas de instalacion por defecto.
    """
    for cmd in ("soffice", "libreoffice"):
        if ruta := shutil.which(cmd):
            return ruta
    for carpeta in (r"C:\Program Files\LibreOffice", r"C:\Program Files (x86)\LibreOffice"):
        exe = Path(carpeta) / "program" / "soffice.exe"
        if exe.is_file():
            return str(exe)
    return None


def _convertir_con_libreoffice(soffice: str, doc: Path, destino: Path) -> Path:
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", str(doc), "--outdir", str(destino)],
            check=True,
            timeout=TIMEOUT_CONVERSION,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise ErrorConversion(f"LibreOffice tardo mas de {TIMEOUT_CONVERSION}s con {doc.name}") from exc
    except subprocess.CalledProcessError as exc:
        raise ErrorConversion(f"LibreOffice fallo con {doc.name}: {exc.stderr.decode(errors='replace').strip()}") from exc
    return destino / f"{doc.stem}.docx"


def _convertir_con_word(doc: Path, destino: Path) -> Path:
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise ErrorConversion(f"{SIN_CONVERSOR} (falta pywin32 para usar Word)") from exc

    salida = destino / f"{doc.stem}.docx"
    pythoncom.CoInitialize()
    try:
        # DispatchEx abre una instancia de Word propia: si el usuario tiene
        # Word abierto, no se toca su sesion ni sus documentos.
        word = win32com.client.DispatchEx("Word.Application")
    except Exception as exc:
        pythoncom.CoUninitialize()
        raise ErrorConversion(SIN_CONVERSOR) from exc
    try:
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone
        documento = word.Documents.Open(
            str(doc.resolve()),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            # Con una contrasena cualquiera, un documento protegido da error en vez
            # de abrir un dialogo que dejaria el script colgado. A los que no
            # tienen contrasena no les afecta.
            PasswordDocument="-",
        )
        try:
            documento.SaveAs2(str(salida.resolve()), FileFormat=16)  # wdFormatXMLDocument
        finally:
            documento.Close(False)
    except Exception as exc:
        raise ErrorConversion(f"Word no pudo convertir {doc.name}: {exc}") from exc
    finally:
        word.Quit()
        pythoncom.CoUninitialize()
    return salida


def convertir_a_docx(doc: Path, destino: Path) -> Path:
    """Convierte un .doc o .rtf a .docx dentro de 'destino' y devuelve la ruta nueva."""
    if soffice := buscar_libreoffice():
        salida = _convertir_con_libreoffice(soffice, doc, destino)
    elif sys.platform == "win32":
        salida = _convertir_con_word(doc, destino)
    else:
        raise ErrorConversion(SIN_CONVERSOR)

    if not salida.is_file():
        raise ErrorConversion(f"La conversion no genero el fichero esperado: {salida}")
    return salida


def crear_conversor() -> DocumentConverter:
    """Conversor de docling limitado a .docx.

    El .docx se lee directamente de su XML, sin modelos de layout ni OCR: los
    niveles de encabezado salen de los estilos (Titulo, Titulo 1, Titulo 2...)
    y las imagenes se cargan siempre, porque van incrustadas en el fichero.

    El unico modelo es DocumentFigureClassifier, que etiqueta cada imagen
    (logo, photograph, bar_chart...): ver extractors.images.clasificacion.
    """
    return DocumentConverter(
        allowed_formats=[InputFormat.DOCX],
        format_options={
            InputFormat.DOCX: WordFormatOption(
                pipeline_options=ConvertPipelineOptions(do_picture_classification=True),
            )
        },
    )


def procesar_docx(
    docx: Path,
    salida: Path,
    conversor: DocumentConverter,
    original: Path | None = None,
    nombre: str | None = None,
) -> dict:
    """Extrae un .docx y escribe su .md, su .json y sus imagenes en 'salida'.

    'original' es el fichero que dio el usuario cuando 'docx' es una copia
    convertida desde .doc o .rtf: de el salen el nombre de las salidas y el
    titulo del arbol, para que no quede rastro del fichero temporal.
    """
    if docx.suffix.lower() != ".docx":
        raise ValueError(f"procesar_docx solo acepta .docx, no {docx.suffix}: usa procesar_word")
    original = original or docx

    doc = conversor.convert(docx).document
    doc.name = original.stem
    return guardar_resultados(doc, original, salida, nombre)


def procesar_word(
    ruta: Path,
    salida: Path,
    conversor: DocumentConverter,
    nombre: str | None = None,
) -> dict:
    """Extrae un .docx, .doc o .rtf; los dos ultimos se convierten antes a .docx."""
    extension = ruta.suffix.lower()
    if extension == ".docx":
        return procesar_docx(ruta, salida, conversor, nombre=nombre)
    if extension not in A_CONVERTIR:
        raise ValueError(f"Formato no soportado: {ruta.suffix}")

    with tempfile.TemporaryDirectory() as temporal:
        docx = convertir_a_docx(ruta, Path(temporal))
        res = procesar_docx(docx, salida, conversor, original=ruta, nombre=nombre)
    res["convertido"] = extension
    return res
