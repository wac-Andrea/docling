#!/usr/bin/env python
"""Extrae el texto de un PDF digital con docling conservando la jerarquia.

Pensado para PDFs con capa de texto nativa: no lleva OCR, asi que es rapido y
el texto sale exactamente como lo escribio el generador del PDF.

Genera tres salidas:
  - <nombre>.md    : texto completo en Markdown (los # reflejan la jerarquia)
  - <nombre>.json  : arbol anidado de secciones con su contenido y pagina
  - por consola    : el indice/esquema del documento

Uso:
    python extraer_pdf.py documento.pdf
    python extraer_pdf.py documento.pdf -o resultados --paginas 1-20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docling.backend.docling_parse_v4_backend import DoclingParseV4DocumentBackend
from docling.backend.pypdfium2_backend import (
    PyPdfiumDocumentBackend,
    PyPdfiumPageBackend,
    pypdfium2_lock,
)
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import (
    DoclingDocument,
    DocItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)


def pagina_de(item: DocItem) -> int | None:
    """Numero de pagina donde aparece el elemento (None si no hay procedencia)."""
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def texto_de(item: DocItem, doc: DoclingDocument) -> str:
    """Texto del elemento; las tablas se serializan como Markdown."""
    if isinstance(item, TableItem):
        return item.export_to_markdown(doc)
    return (getattr(item, "text", "") or "").strip()


def construir_arbol(doc: DoclingDocument) -> dict:
    """Recorre el documento y lo convierte en un arbol de secciones anidadas.

    Nivel 0 = documento, 1 = titulo del documento, 2+ = encabezados de seccion.
    La profundidad sale del 'level' que asigna el modelo de layout de docling.
    """
    raiz = {
        "titulo": doc.name,
        "nivel": 0,
        "pagina": None,
        "contenido": [],
        "subsecciones": [],
    }
    pila = [raiz]

    # iterate_items solo recorre la capa "body": los encabezados y pies de
    # pagina repetidos quedan fuera automaticamente.
    for item, _ in doc.iterate_items():
        if isinstance(item, (TitleItem, SectionHeaderItem)):
            nivel = 1 if isinstance(item, TitleItem) else item.level + 1
            nodo = {
                "titulo": texto_de(item, doc),
                "nivel": nivel,
                "pagina": pagina_de(item),
                "contenido": [],
                "subsecciones": [],
            }
            # Cerramos las secciones abiertas de nivel igual o mas profundo.
            while len(pila) > 1 and pila[-1]["nivel"] >= nivel:
                pila.pop()
            pila[-1]["subsecciones"].append(nodo)
            pila.append(nodo)

        elif isinstance(item, (TextItem, TableItem)):
            texto = texto_de(item, doc)
            if texto:
                pila[-1]["contenido"].append(
                    {
                        "tipo": item.label.value,  # text, list_item, table, caption...
                        "pagina": pagina_de(item),
                        "texto": texto,
                    }
                )

    return raiz


def formatear_indice(nodo: dict, lineas: list[str] | None = None) -> str:
    """Devuelve el esquema del documento como texto indentado."""
    lineas = [] if lineas is None else lineas
    if nodo["nivel"] > 0:
        sangria = "  " * (nodo["nivel"] - 1)
        pagina = f"  (p. {nodo['pagina']})" if nodo["pagina"] else ""
        lineas.append(f"{sangria}{nodo['titulo']}{pagina}")
    for hijo in nodo["subsecciones"]:
        formatear_indice(hijo, lineas)
    return "\n".join(lineas)


def esta_vacio(arbol: dict) -> bool:
    """True si no se extrajo ni un bloque de texto en todo el arbol."""
    tiene_encabezado = arbol["nivel"] > 0 and bool(arbol["titulo"])
    if arbol["contenido"] or tiene_encabezado:
        return False
    return all(esta_vacio(hijo) for hijo in arbol["subsecciones"])


# Altura minima, en puntos, para que una celda de texto se considere real.
#
# pypdfium2 emite de vez en cuando rectangulos degenerados: astillas de altura
# casi nula que no contienen letra propia, pero que se solapan con el glifo
# vecino. docling les pide su texto y acaba duplicando ese caracter (un titulo
# '... JIT Y LEAN' sale como 'LEAN N').
#
# El umbral esta medido, no elegido a ojo. Sobre 477 paginas de documentos
# reales: astillas = 0.014 pt, y el glifo legitimo mas plano (un guion) = 0.587
# pt. 0.1 deja un factor 6 de margen por los dos lados. Subirlo hasta 1.0
# empezaria a borrar guiones, rayas y signos menos.
ALTURA_MINIMA_CELDA = 0.1


class _PaginaPypdfiumLimpia(PyPdfiumPageBackend):
    """Pagina de pypdfium2 sin los rectangulos degenerados."""

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


def crear_conversor(backend: str = "pypdfium2") -> DocumentConverter:
    """Conversor de docling con el OCR desactivado.

    El texto se lee de la capa nativa del PDF, que es exacta y rapida. Un PDF
    escaneado no tiene esa capa y saldria vacio; main() avisa si pasa.
    """
    opciones = PdfPipelineOptions(do_ocr=False)
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="Ruta del PDF a procesar")
    parser.add_argument("-o", "--salida", type=Path, default=Path("salida"), help="Directorio de salida (por defecto: ./salida)")
    parser.add_argument("--paginas", type=parsear_paginas, default=(1, 2**31), help="Rango de paginas, p.ej. 1-20")
    parser.add_argument("--backend", choices=list(BACKENDS), default="pypdfium2", help="Lector de la capa de texto (por defecto: pypdfium2; prueba docling-parse si algun documento sale raro)")
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"No encuentro el fichero: {args.pdf}")

    print(f"Procesando {args.pdf.name}...")
    resultado = crear_conversor(args.backend).convert(args.pdf, page_range=args.paginas)
    doc = resultado.document

    args.salida.mkdir(parents=True, exist_ok=True)
    base = args.salida / args.pdf.stem

    # 1) Markdown: el texto completo con la jerarquia en forma de #, ##, ###...
    md = base.with_suffix(".md")
    md.write_text(doc.export_to_markdown(), encoding="utf-8")

    # 2) JSON: arbol de secciones para procesarlo despues (RAG, resumenes, etc.)
    arbol = construir_arbol(doc)
    js = base.with_suffix(".json")
    js.write_text(json.dumps(arbol, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) Indice por consola
    print(f"\nPaginas procesadas: {len(doc.pages)}")
    if esta_vacio(arbol):
        print(
            "\nAVISO: no se extrajo nada de texto. El PDF no parece digital,\n"
            "sino escaneado (paginas como imagen). Este script no lleva OCR."
        )
    print("\n--- Estructura del documento ---")
    print(formatear_indice(arbol) or "(el documento no tiene encabezados detectados)")
    print(f"\nGenerado:\n  {md}\n  {js}")


if __name__ == "__main__":
    main()
