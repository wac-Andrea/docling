"""Tablas: su contenido en Markdown, su localizacion y sus duplicados."""

from __future__ import annotations

from pathlib import Path

from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
from docling_core.types.doc import DoclingDocument, DocItem, TableItem

from .links import localizacion


def texto_tabla(item: TableItem, doc: DoclingDocument) -> str:
    """La tabla serializada como Markdown, con su pie encima si lo tiene.

    Equivale a item.export_to_markdown(doc), pero sin la clase de las imagenes
    que haya en sus celdas: el serializador la escribiria como texto ('Logo',
    'Photograph') y acabaria indexada como si fuera contenido de la tabla.
    """
    params = MarkdownParams(include_picture_classification=False)
    return MarkdownDocSerializer(doc=doc, params=params).serialize(item=item).text


def ya_en_tabla(item: DocItem, doc: DoclingDocument) -> bool:
    """True si el texto del elemento ya va en el Markdown de su tabla.

    docling cuelga de la tabla elementos que iterate_items tambien devuelve:
      - las celdas con formato (negrita, varios parrafos, listas, tablas
        anidadas...), que guarda dos veces: como texto de la celda y como
        elemento hijo.
      - el pie de la tabla, que export_to_markdown pone encima de la tabla.
      - las notas de la tabla, que export_to_markdown NO incluye: esas no
        estan repetidas y hay que conservarlas.
    """
    nodo = item
    while nodo.parent is not None:
        padre = nodo.parent.resolve(doc)
        if isinstance(padre, TableItem):
            return nodo.self_ref not in {ref.cref for ref in padre.footnotes}
        nodo = padre
    return False


def bloque_tabla(item: TableItem, doc: DoclingDocument, documento: Path, numero: int) -> dict:
    """Describe una tabla: su contenido en Markdown y donde esta.

    'numero' es su orden entre las tablas del documento (Tabla 1, Tabla 2...).
    """
    pagina, url, posicion = localizacion(item, documento)
    bloque = {
        "tipo": "table",
        "numero": numero,
        "pagina": pagina,
        "url": url,
        "texto": texto_tabla(item, doc),
    }
    if posicion:
        bloque["posicion"] = posicion
    return bloque
