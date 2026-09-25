"""Lo comun a todos los procesadores: del DoclingDocument a las salidas.

docling convierte cualquier formato de entrada -- PDF, Word... -- al mismo
modelo interno, DoclingDocument. Lo que cambia entre formatos es como se
obtiene ese modelo (cada procesador); como se recorre y que se escribe es
igual para todos, y esta aqui.
"""

from __future__ import annotations

import json
from pathlib import Path

from docling_core.types.doc import (
    DoclingDocument,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)

from ..extractors.images import GuardadorImagenes, bloque_imagen
from ..extractors.tables import bloque_tabla, texto_tabla, ya_en_tabla
from ..extractors.text import bloque_texto, grupo_en_linea, nodo_seccion, texto_de, titulo_documento, unir_trozos


def construir_arbol(doc: DoclingDocument, documento: Path, guardador: GuardadorImagenes | None = None) -> dict:
    """Recorre el documento y lo convierte en un arbol de secciones anidadas.

    'documento' es el fichero original, del que salen las URLs por pagina.
    Nivel 0 = documento; los demas niveles, ver extractors.text.nodo_seccion.
    """
    raiz = {
        "document": titulo_documento(doc),
        "titulo": doc.name,
        "nivel": 0,
        "pagina": None,
        "contenido": [],
        "subsecciones": [],
    }
    pila = [raiz]
    # Parrafo partido que se esta recomponiendo: (su InlineGroup, su bloque).
    parrafo_abierto: tuple[str, dict] | None = None
    # Orden de cada tabla e imagen en el documento: su referencia en un Word,
    # que no tiene paginas.
    num_tablas = num_imagenes = 0

    # iterate_items solo recorre la capa "body": los encabezados y pies de
    # pagina repetidos quedan fuera automaticamente.
    for item, _ in doc.iterate_items():
        # Lo que cuelga de una tabla ya va casi todo en su bloque 'table'
        # (celdas, pie). Las imagenes no: de ellas la tabla solo deja la marca
        # '<!-- image -->', asi que se conservan como bloque 'picture' propio,
        # justo detras de su tabla.
        if not isinstance(item, PictureItem) and ya_en_tabla(item, doc):
            continue

        if isinstance(item, (TitleItem, SectionHeaderItem)):
            nodo = nodo_seccion(item)
            # Cerramos las secciones abiertas de nivel igual o mas profundo.
            while len(pila) > 1 and pila[-1]["nivel"] >= nodo["nivel"]:
                pila.pop()
            pila[-1]["subsecciones"].append(nodo)
            pila.append(nodo)

        elif isinstance(item, PictureItem):
            num_imagenes += 1
            pila[-1]["contenido"].append(bloque_imagen(item, doc, documento, num_imagenes, guardador))

        elif isinstance(item, TableItem):
            if texto_tabla(item, doc):
                num_tablas += 1
                pila[-1]["contenido"].append(bloque_tabla(item, doc, documento, num_tablas))

        elif isinstance(item, TextItem):
            texto = texto_de(item)
            if not texto:
                continue

            # Los trozos de un parrafo partido se juntan en un solo bloque.
            grupo = grupo_en_linea(item, doc)
            if grupo is not None and parrafo_abierto and parrafo_abierto[0] == grupo.self_ref:
                bloque = parrafo_abierto[1]
                bloque["texto"] = unir_trozos(bloque["texto"], texto)
                continue

            bloque = bloque_texto(item, doc, texto, grupo)
            pila[-1]["contenido"].append(bloque)
            parrafo_abierto = (grupo.self_ref, bloque) if grupo is not None else None

    return raiz


def guardar_resultados(doc: DoclingDocument, original: Path, salida: Path, nombre: str | None = None) -> dict:
    """Escribe el .md, el .json y las imagenes de un documento en 'salida'.

    'original' es el fichero que dio el usuario: de el salen las URLs. 'nombre'
    es el de los ficheros de salida (por defecto, el del original sin
    extension); el detector lo cambia si dos documentos se llaman igual.

    Devuelve un resumen con lo extraido.
    """
    nombre = nombre or original.stem
    salida.mkdir(parents=True, exist_ok=True)

    md = salida / f"{nombre}.md"
    # Sin la clase de cada imagen: docling la escribiria como texto ('Logo',
    # 'Photograph') delante de la imagen. Esa informacion va en el .json.
    md.write_text(doc.export_to_markdown(include_picture_classification=False), encoding="utf-8")

    arbol = construir_arbol(doc, original, GuardadorImagenes(salida / "imagenes" / nombre))
    js = salida / f"{nombre}.json"
    js.write_text(json.dumps(arbol, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "documento": original,
        "arbol": arbol,
        "md": md,
        "json": js,
        "imagenes": recopilar(arbol, "picture"),
        "vacio": esta_vacio(arbol),
    }


# --- Consultas sobre el arbol ya construido ---------------------------------


def recopilar(nodo: dict, tipo: str) -> list[dict]:
    """Todos los bloques de un tipo del arbol, en orden de lectura."""
    propios = [b for b in nodo["contenido"] if b["tipo"] == tipo]
    return propios + [b for hijo in nodo["subsecciones"] for b in recopilar(hijo, tipo)]


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
    """True si no se extrajo ni un bloque de texto en todo el arbol.

    Las imagenes no cuentan: un PDF escaneado son paginas-imagen, y si contaran
    pareceria que se extrajo algo cuando en realidad no hay texto ninguno.
    """
    tiene_texto = any(b["tipo"] != "picture" for b in arbol["contenido"])
    tiene_encabezado = arbol["nivel"] > 0 and bool(arbol["titulo"])
    if tiene_texto or tiene_encabezado:
        return False
    return all(esta_vacio(hijo) for hijo in arbol["subsecciones"])


def tiene_secciones(nodo: dict) -> bool:
    """True si el arbol tiene al menos un encabezado de seccion.

    El titulo del documento (nivel 1) no cuenta: un documento con titulo pero
    sin encabezados sigue teniendo todo el texto en un unico bloque.
    """
    return nodo["nivel"] >= 2 or any(tiene_secciones(hijo) for hijo in nodo["subsecciones"])
