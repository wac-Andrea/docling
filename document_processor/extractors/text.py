"""Texto: titulos de seccion, parrafos y puntos de lista."""

from __future__ import annotations

from docling_core.types.doc import (
    DoclingDocument,
    DocItem,
    InlineGroup,
    ListGroup,
    ListItem,
    SectionHeaderItem,
    TextItem,
    TitleItem,
)


def pagina_de(item: DocItem) -> int | None:
    """Numero de pagina donde aparece el elemento (None si no hay procedencia)."""
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def texto_de(item: DocItem) -> str:
    """Texto del elemento, sin espacios en los bordes."""
    return (getattr(item, "text", "") or "").strip()


def nodo_seccion(item: DocItem) -> dict:
    """Nodo del arbol para un titulo o encabezado de seccion.

    Nivel 1 = titulo del documento, 2+ = encabezados de seccion. La profundidad
    sale del 'level' que asigna docling: en un PDF lo decide su modelo de
    layout; en un Word, los estilos de titulo del documento.
    """
    nivel = 1 if isinstance(item, TitleItem) else item.level + 1
    return {
        "titulo": texto_de(item),
        "nivel": nivel,
        "pagina": pagina_de(item),
        "contenido": [],
        "subsecciones": [],
    }


def titulo_documento(doc: DoclingDocument) -> str | None:
    """Titulo del documento, o None si no se reconoce ninguno.

    Si docling marca un titulo (un Word con el estilo 'Titulo'), es ese. En un
    PDF el modelo de layout no lo distingue: sale como un encabezado mas, asi
    que se toma el primer encabezado si esta en la primera pagina. Los
    metadatos del fichero no sirven: suelen venir vacios o con cosas como
    'Diapositiva 1' o 'Microsoft Word - informe.doc'.
    """
    encabezado = None
    for item, _ in doc.iterate_items():
        if isinstance(item, TitleItem) and texto_de(item):
            return texto_de(item)
        if encabezado is None and isinstance(item, SectionHeaderItem) and texto_de(item):
            encabezado = item
    if encabezado is not None and pagina_de(encabezado) == 1:
        return texto_de(encabezado)
    return None


def grupo_en_linea(item: DocItem, doc: DoclingDocument) -> InlineGroup | None:
    """Parrafo al que pertenece el elemento si es un trozo de uno partido.

    docling parte en trozos los parrafos con formatos mezclados (una palabra en
    negrita, un enlace...) para no perder ese formato: cada trozo es un
    elemento distinto, y todos cuelgan de un mismo InlineGroup.
    """
    padre = item.parent.resolve(doc) if item.parent else None
    return padre if isinstance(padre, InlineGroup) else None


# Signos que van pegados al trozo anterior o al siguiente, sin espacio.
SIN_ESPACIO_ANTES = tuple(",.;:)]}»”?!%…")
SIN_ESPACIO_DESPUES = tuple("([{«“¿¡")


def unir_trozos(anterior: str, siguiente: str) -> str:
    """Une dos trozos de un parrafo partido.

    docling guarda cada trozo sin los espacios de los bordes, asi que no se
    sabe si en el original habia uno entre ellos. Se pone uno, salvo junto a
    signos de puntuacion: 'extranjeros' + ', con el fin' no debe dar
    'extranjeros , con el fin'.
    """
    if siguiente.startswith(SIN_ESPACIO_ANTES) or anterior.endswith(SIN_ESPACIO_DESPUES):
        return anterior + siguiente
    return f"{anterior} {siguiente}"


def nivel_de_lista(punto: ListItem, doc: DoclingDocument) -> int:
    """Profundidad del punto: 1 = lista principal, 2 = sublista, etc.

    Cada lista es un ListGroup, y una sublista cuelga del punto que la
    contiene, asi que basta con contar los ListGroup que hay por encima.
    Con ella se reconstruye quien es padre de quien, igual que con el
    'nivel' de las secciones: el padre de un punto es el ultimo punto
    anterior con un nivel menos.
    """
    nivel, padre = 0, punto.parent
    while padre is not None:
        nodo = padre.resolve(doc)
        if isinstance(nodo, ListGroup):
            nivel += 1
        padre = nodo.parent
    return max(nivel, 1)


def bloque_texto(item: TextItem, doc: DoclingDocument, texto: str, grupo: InlineGroup | None) -> dict:
    """Bloque de un parrafo o de un punto de lista.

    'grupo' es el InlineGroup si 'item' es el primer trozo de un parrafo
    partido; los trozos siguientes los va anadiendo construir_arbol.
    """
    # Punto de lista al que pertenece el texto: el propio elemento o, si es un
    # parrafo partido, el ListItem del que cuelgan los trozos (llega vacio: su
    # texto va en ellos).
    punto = item if isinstance(item, ListItem) else None
    if grupo is not None and isinstance(grupo.parent.resolve(doc), ListItem):
        punto = grupo.parent.resolve(doc)

    if punto is not None:
        return {
            "tipo": "list_item",
            "nivel": nivel_de_lista(punto, doc),
            "marcador": (punto.marker or "").strip(),
            "pagina": pagina_de(item),
            "texto": texto,
        }
    return {"tipo": item.label.value, "pagina": pagina_de(item), "texto": texto}  # text, caption...
