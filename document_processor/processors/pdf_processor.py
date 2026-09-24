"""PDF digital -> DoclingDocument, con la capa de texto nativa y sin OCR.

Pensado para PDFs con capa de texto: sin OCR es rapido y el texto sale
exactamente como lo escribio el generador del PDF. Un PDF escaneado no tiene
esa capa y saldria vacio; el detector lo descarta antes con su auditoria.
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

from ..detector import ALTURA_MINIMA_CELDA
from .base import guardar_resultados


class _PaginaPypdfiumLimpia(PyPdfiumPageBackend):
    """Pagina de pypdfium2 sin los rectangulos degenerados (astillas)."""

    def _compute_text_cells(self):
        celdas = super()._compute_text_cells()
        return [c for c in celdas if abs(c.rect.to_bounding_box().height) >= ALTURA_MINIMA_CELDA]


class PyPdfiumLimpioBackend(PyPdfiumDocumentBackend):
    """pypdfium2 con el filtro de astillas aplicado a cada pagina."""

    def load_page(self, page_no: int) -> PyPdfiumPageBackend:
        with pypdfium2_lock:
            return _PaginaPypdfiumLimpia(self._pdoc, self.document_hash, page_no)


# Quien lee la capa de texto del PDF. No es un detalle menor: el backend por
# defecto de docling ('docling-parse') se deja caracteres por el camino en
# algunos PDFs -- tildes sueltas, letras de titulos -- que pypdfium2 si lee.
BACKENDS = {
    "pypdfium2": PyPdfiumLimpioBackend,
    "pypdfium2-crudo": PyPdfiumDocumentBackend,
    "docling-parse": DoclingParseV4DocumentBackend,
}

TODAS_LAS_PAGINAS = (1, 2**31)


def crear_conversor(backend: str = "pypdfium2", escala: float = 2.0) -> DocumentConverter:
    """Conversor de docling para PDF, con el OCR desactivado.

    docling recorta ademas la region de cada figura para guardarla como PNG.
    Recorta la zona detectada, no el bitmap incrustado: asi tambien salen los
    diagramas hechos con vectores, que una extraccion de imagenes embebidas no ve.

    Cada figura pasa por DocumentFigureClassifier, que la etiqueta (logo,
    photograph, bar_chart...): ver extractors.images.clasificacion.

    El modelo de layout solo marca que algo es un titulo, no su nivel: sin mas,
    todos los titulos de un PDF salen al mismo nivel. La etapa de jerarquia de
    titulos se lo asigna con tres senales, por prioridad: marcadores del PDF
    (su indice navegable), numeracion (8. > 8.1 > 8.1.1 > a.) y estilo. Acierta
    sobre todo con titulos numerados; mezclando numerados y sin numerar puede
    colgar un apartado de otro que es su hermano. Solo cambia niveles: el
    texto de cada seccion no varia, solo la ruta de titulos padre.

    El estilo necesita las paginas analizadas (generate_parsed_pages). Con el
    lector pypdfium2 no hay nombre de fuente, asi que ahi solo cuenta el tamano
    de letra, no la negrita ni la cursiva.
    """
    opciones = PdfPipelineOptions(do_ocr=False)
    opciones.generate_picture_images = True
    opciones.images_scale = escala
    opciones.do_picture_classification = True
    opciones.heading_hierarchy_options = HeadingHierarchyOptions(enabled=True)
    opciones.generate_parsed_pages = True
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=opciones,
                backend=BACKENDS[backend],
            )
        }
    )


def parsear_paginas(valor: str) -> tuple[int, int]:
    """Convierte '3' o '1-20' en el rango que espera docling."""
    if "-" in valor:
        inicio, fin = valor.split("-", 1)
        return int(inicio), int(fin)
    return int(valor), int(valor)


def procesar_pdf(
    pdf: Path,
    salida: Path,
    conversor: DocumentConverter,
    paginas: tuple[int, int] = TODAS_LAS_PAGINAS,
    nombre: str | None = None,
) -> dict:
    """Extrae un PDF y escribe su .md, su .json y sus imagenes en 'salida'.

    Recibe el conversor ya construido en vez de crearlo: cargar los modelos de
    layout cuesta varios segundos, y al procesar una carpeta interesa pagarlo
    una sola vez para todos los documentos.
    """
    doc = conversor.convert(pdf, page_range=paginas).document
    res = guardar_resultados(doc, pdf, salida, nombre)
    res["paginas"] = len(doc.pages)
    return res
