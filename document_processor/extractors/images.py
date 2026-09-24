"""Imagenes: su localizacion, su pie y el PNG guardado."""

from __future__ import annotations

import hashlib
from pathlib import Path

from docling_core.types.doc import DoclingDocument, PictureItem

from .links import localizacion, url_de_fichero


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
        n = self._contador[pagina]
        # Un .docx no tiene paginas: numeracion corrida 'image{n}.png', que es
        # como nombra las imagenes docx2python en el extractor de Word de WAC_DataLib.
        nombre = f"image{pagina}_{n}.png" if pagina is not None else f"image{n}.png"
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
        return url_de_fichero(self.destino / nombre)


def clasificacion(item: PictureItem) -> tuple[str | None, float | None]:
    """Tipo de imagen segun DocumentFigureClassifier y su confianza (0-1).

    El modelo reparte la probabilidad entre 26 clases (logo, icon, photograph,
    bar_chart, engineering_drawing, screenshot_from_computer...) y las devuelve
    ordenadas de mayor a menor. Sirve para descartar logos y adornos de
    maquetacion antes de indexar. Con confianza baja (p. ej. 'logo' 0.47 frente
    a 'icon' 0.30) la clase es dudosa y conviene no filtrar solo por ella.
    """
    clasif = item.meta.classification if item.meta else None
    if not clasif or not clasif.predictions:
        return None, None
    mejor = max(clasif.predictions, key=lambda p: p.confidence)
    return mejor.class_name, round(mejor.confidence, 3)


def bloque_imagen(
    item: PictureItem,
    doc: DoclingDocument,
    documento: Path,
    numero: int,
    guardador: GuardadorImagenes | None = None,
) -> dict:
    """Describe una imagen: donde esta, que es, como de grande y que pie tiene.

    La 'clase' (logo, photograph, bar_chart...) y el tamano sirven para
    filtrar despues los adornos de maquetacion (vinetas, logos de pie de pagina).

    'numero' es su orden entre las imagenes del documento.
    """
    pagina, url, posicion = localizacion(item, documento)
    clase, confianza = clasificacion(item)
    bloque = {
        "tipo": "picture",
        "numero": numero,
        "pagina": pagina,
        "url": url,
        "texto": (item.caption_text(doc) or "").strip(),
        "clase": clase,
        "confianza": confianza,
    }
    if posicion:
        bloque["posicion"] = posicion
    if guardador is not None:
        nombre = guardador.guardar(item, doc, pagina)
        bloque["archivo"] = nombre          # nombre a secas (convencion WAC_DataLib)
        bloque["url_imagen"] = guardador.url_de(nombre)  # ruta pinchable al PNG
    return bloque
