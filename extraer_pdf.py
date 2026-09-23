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
import hashlib
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
    PictureItem,
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


def url_de_pagina(pdf: Path, pagina: int | None) -> str | None:
    """URL que abre el PDF directamente en esa pagina.

    El fragmento '#page=N' es un parametro estandar de apertura de PDF: lo
    entienden los navegadores y Acrobat, asi que el enlace es pinchable.
    """
    if pagina is None:
        return None
    return f"{pdf.resolve().as_uri()}#page={pagina}"


class GuardadorImagenes:
    """Guarda las imagenes detectadas y devuelve el nombre de cada fichero.

    Convencion de nombres tomada de WAC_DataLib (file2text/extractors/pdf.py):
    'image{pagina}_{n}.png', para que los ficheros sean intercambiables con lo
    que produce su extract_images().

    Dos imagenes identicas comparten fichero -- un logo repetido no se escribe
    40 veces -- pero cada aparicion conserva su pagina en el JSON. La libreria
    original descarta los duplicados por completo y con ellos su localizacion,
    que es justo el dato que aqui interesa.
    """

    def __init__(self, destino: Path):
        self.destino = destino
        self.destino.mkdir(parents=True, exist_ok=True)
        self._por_hash: dict[str, str] = {}
        self._contador: dict[int, int] = {}

    def guardar(self, item: PictureItem, doc: DoclingDocument, pagina: int | None) -> str | None:
        imagen = item.get_image(doc)
        if imagen is None:
            return None

        huella = hashlib.md5(imagen.tobytes()).hexdigest()
        if huella in self._por_hash:
            return self._por_hash[huella]

        self._contador[pagina] = self._contador.get(pagina, 0) + 1
        nombre = f"image{pagina}_{self._contador[pagina]}.png"
        imagen.save(self.destino / nombre)
        self._por_hash[huella] = nombre
        return nombre

    def url_de(self, nombre: str | None) -> str | None:
        """URL pinchable del PNG ya guardado.

        'archivo' se queda con el nombre a secas, que es la convencion de
        WAC_DataLib; esta es la ruta completa para poder abrirlo.
        """
        if nombre is None:
            return None
        return (self.destino / nombre).resolve().as_uri()


def bloque_imagen(
    item: PictureItem,
    doc: DoclingDocument,
    pdf: Path,
    guardador: GuardadorImagenes | None = None,
) -> dict:
    """Describe una imagen: donde esta, como de grande y que pie tiene.

    No extrae los bytes de la imagen, solo su localizacion. El tamano sirve
    para filtrar despues: los adornos de maquetacion (vinetas, logos de pie de
    pagina) salen con pocos puntos de lado.
    """
    prov = item.prov[0] if item.prov else None
    pagina = prov.page_no if prov else None
    bloque = {
        "tipo": "picture",
        "pagina": pagina,
        "url": url_de_pagina(pdf, pagina),
        "texto": (item.caption_text(doc) or "").strip(),
    }
    if prov:
        caja = prov.bbox
        bloque["posicion"] = {
            "x": round(caja.l, 1),
            "y": round(caja.b, 1),
            "ancho": round(abs(caja.r - caja.l), 1),
            "alto": round(abs(caja.t - caja.b), 1),
        }
    if guardador is not None:
        nombre = guardador.guardar(item, doc, pagina)
        bloque["archivo"] = nombre          # nombre a secas (convencion WAC_DataLib)
        bloque["url_imagen"] = guardador.url_de(nombre)  # ruta pinchable al PNG
    return bloque


def construir_arbol(doc: DoclingDocument, pdf: Path, guardador: GuardadorImagenes | None = None) -> dict:
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

        elif isinstance(item, PictureItem):
            pila[-1]["contenido"].append(bloque_imagen(item, doc, pdf, guardador))

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


def recopilar_imagenes(nodo: dict, encontradas: list[dict] | None = None) -> list[dict]:
    """Devuelve todas las imagenes del arbol, en orden de lectura."""
    encontradas = [] if encontradas is None else encontradas
    encontradas.extend(b for b in nodo["contenido"] if b["tipo"] == "picture")
    for hijo in nodo["subsecciones"]:
        recopilar_imagenes(hijo, encontradas)
    return encontradas


def esta_vacio(arbol: dict) -> bool:
    """True si no se extrajo ni un bloque de texto en todo el arbol.

    Las imagenes no cuentan: un PDF escaneado son paginas-imagen, y si contaran
    pareceria que se extrajo algo cuando en realidad no hay texto ninguno.
    """
    tiene_texto = any(b["tipo"] != "picture" for b in arbol["contenido"])
    tiene_encabezado = arbol["nivel"] > 0 and bool(arbol["titulo"])
    if tiene_texto or tiene_encabezado:
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


def crear_conversor(backend: str = "pypdfium2", imagenes: bool = False, escala: float = 2.0) -> DocumentConverter:
    """Conversor de docling con el OCR desactivado.

    El texto se lee de la capa nativa del PDF, que es exacta y rapida. Un PDF
    escaneado no tiene esa capa y saldria vacio; main() avisa si pasa.

    Con 'imagenes' docling recorta ademas la region de cada figura. Recorta la
    zona detectada, no el bitmap incrustado: asi tambien salen los diagramas
    hechos con vectores, que una extraccion de imagenes embebidas no ve.
    """
    opciones = PdfPipelineOptions(do_ocr=False)
    if imagenes:
        opciones.generate_picture_images = True
        opciones.images_scale = escala
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
    paginas: tuple[int, int] = (1, 2**31),
    imagenes: bool = False,
) -> dict:
    """Extrae un PDF y escribe su .md y su .json en 'salida'.

    Recibe el conversor ya construido en vez de crearlo: cargar los modelos de
    layout cuesta varios segundos, y al procesar una carpeta interesa pagarlo
    una sola vez para todos los documentos.

    Devuelve un resumen con lo extraido.
    """
    doc = conversor.convert(pdf, page_range=paginas).document

    salida.mkdir(parents=True, exist_ok=True)
    base = salida / pdf.stem

    md = base.with_suffix(".md")
    md.write_text(doc.export_to_markdown(), encoding="utf-8")

    guardador = GuardadorImagenes(salida / "imagenes" / pdf.stem) if imagenes else None
    arbol = construir_arbol(doc, pdf, guardador)
    js = base.with_suffix(".json")
    js.write_text(json.dumps(arbol, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "pdf": pdf,
        "arbol": arbol,
        "md": md,
        "json": js,
        "paginas": len(doc.pages),
        "imagenes": recopilar_imagenes(arbol),
        "vacio": esta_vacio(arbol),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path, help="Ruta del PDF a procesar")
    parser.add_argument("-o", "--salida", type=Path, default=Path("salida"), help="Directorio de salida (por defecto: ./salida)")
    parser.add_argument("--paginas", type=parsear_paginas, default=(1, 2**31), help="Rango de paginas, p.ej. 1-20")
    parser.add_argument("--backend", choices=list(BACKENDS), default="pypdfium2", help="Lector de la capa de texto (por defecto: pypdfium2; prueba docling-parse si algun documento sale raro)")
    parser.add_argument("--imagenes", action="store_true", help="Guarda tambien las imagenes como PNG en <salida>/imagenes/")
    parser.add_argument("--escala-imagen", type=float, default=2.0, help="Resolucion de las imagenes guardadas (2 = ~144 ppp)")
    args = parser.parse_args()

    if not args.pdf.is_file():
        raise SystemExit(f"No encuentro el fichero: {args.pdf}")

    print(f"Procesando {args.pdf.name}...")
    conversor = crear_conversor(args.backend, args.imagenes, args.escala_imagen)
    res = procesar_pdf(args.pdf, args.salida, conversor, args.paginas, args.imagenes)
    arbol = res["arbol"]

    print(f"\nPaginas procesadas: {res['paginas']}")
    if res["vacio"]:
        print(
            "\nAVISO: no se extrajo nada de texto. El PDF no parece digital,\n"
            "sino escaneado (paginas como imagen). Este script no lleva OCR."
        )
    print("\n--- Estructura del documento ---")
    print(formatear_indice(arbol) or "(el documento no tiene encabezados detectados)")

    imagenes = res["imagenes"]
    if imagenes:
        print(f"\n--- Imagenes: {len(imagenes)} ---")
        for img in imagenes:
            pos = img.get("posicion", {})
            medidas = f"{pos.get('ancho', '?')}x{pos.get('alto', '?')} pt" if pos else ""
            fichero = f"  {img['archivo']}" if img.get("archivo") else ""
            print(f"  p.{img['pagina']:<4} {medidas:<16} {img['url']}{fichero}")

    print(f"\nGenerado:\n  {res['md']}\n  {res['json']}")


if __name__ == "__main__":
    main()
