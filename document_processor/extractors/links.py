"""Enlaces y localizacion de cada elemento en el documento original.

Todas las URLs que aparecen en el .json salen de aqui:
  - 'url'        : abre el documento original en la pagina del elemento.
  - 'url_imagen' : ruta al PNG guardado de una imagen.
Y tambien la 'posicion' (caja en puntos) de imagenes y tablas.
"""

from __future__ import annotations

from pathlib import Path

from docling_core.types.doc import DocItem


def url_de_fichero(ruta: Path) -> str:
    """URL file:// pinchable de un fichero local."""
    return ruta.resolve().as_uri()


def url_de_pagina(documento: Path, pagina: int | None) -> str | None:
    """URL que abre el documento directamente en esa pagina.

    El fragmento '#page=N' es un parametro estandar de apertura de PDF: lo
    entienden los navegadores y Acrobat, asi que el enlace es pinchable. Los
    formatos sin paginas (Word) llegan con pagina None y no tienen URL.
    """
    if pagina is None:
        return None
    return f"{url_de_fichero(documento)}#page={pagina}"


def localizacion(item: DocItem, documento: Path) -> tuple[int | None, str | None, dict | None]:
    """Donde esta el elemento en el original: pagina, enlace a ella y caja.

    Solo los formatos con paginas (PDF) tienen esta informacion. En un Word
    los tres valores salen a None, y la unica referencia es el 'numero' de
    orden que pone construir_arbol.
    """
    prov = item.prov[0] if item.prov else None
    if prov is None:
        return None, None, None
    caja = prov.bbox
    posicion = {
        "x": round(caja.l, 1),
        "y": round(caja.b, 1),
        "ancho": round(abs(caja.r - caja.l), 1),
        "alto": round(abs(caja.t - caja.b), 1),
    }
    return prov.page_no, url_de_pagina(documento, prov.page_no), posicion
