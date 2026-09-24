"""Que es cada fichero y si se puede procesar, antes de extraer nada.

  - Tipo real de cada fichero (PDF o Word) por su contenido, no solo por la
    extension: los renombrados o danados se saltan antes de gastar tiempo.
  - Nombres de salida sin choques cuando dos documentos se llaman igual.
  - Auditoria de la capa de texto de los PDF: marca los que no se van a poder
    extraer (escaneados, ilegibles) para no procesarlos a ciegas.

La auditoria tambien se puede lanzar sola, sin extraer:
    python -m document_processor.detector carpeta/ --paginas 10 --csv informe.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

# Primeros bytes que debe tener cada formato. La extension la puede cambiar
# cualquiera; el contenido no engana. Un .doc puede ser en realidad un RTF --
# Word guarda asi a veces --, y el conversor lo abre igual.
FIRMAS = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
    ".doc": (b"\xd0\xcf\x11\xe0", b"{\\rtf"),
    ".rtf": (b"{\\rtf",),
}


def tipo_de(ruta: Path) -> str | None:
    """'pdf' o 'word' si el contenido cuadra con la extension; si no, None.

    Detecta ficheros renombrados o danados antes de gastar tiempo en ellos. Un
    .docx protegido con contrasena tampoco pasa: va cifrado dentro de un
    contenedor que no es el de un .docx, y docling no podria abrirlo.
    """
    extension = ruta.suffix.lower()
    firmas = FIRMAS.get(extension)
    if firmas is None:
        return None
    with ruta.open("rb") as f:
        cabecera = f.read(1024)
    if extension == ".pdf":
        # La norma permite algo de basura antes de '%PDF' en el primer KB.
        valido = b"%PDF" in cabecera
    else:
        valido = cabecera.startswith(firmas)
    if not valido:
        return None
    return "pdf" if extension == ".pdf" else "word"


def listar_documentos(ruta: Path) -> list[Path]:
    """Documentos de una carpeta (con subcarpetas), o el fichero suelto.

    Se saltan los '~$...' que deja Word mientras un documento esta abierto:
    tienen extension .docx pero son ficheros de bloqueo, no documentos.
    """
    if ruta.is_file():
        return [ruta]
    return sorted(
        p for p in ruta.rglob("*")
        if p.is_file() and p.suffix.lower() in FIRMAS and not p.name.startswith("~$")
    )


def nombres_de_salida(documentos: list[Path]) -> dict[Path, str]:
    """Nombre de los ficheros de salida de cada documento, sin choques.

    Si en la carpeta estan 'informe.pdf' e 'informe.docx' -- un Word y el PDF
    exportado de el, algo habitual --, los dos escribirian 'informe.json'.
    En ese caso se anade la extension: 'informe-pdf.json', 'informe-docx.json'.
    Se compara sin mayusculas porque en Windows 'A.json' y 'a.json' son el
    mismo fichero.
    """
    repeticiones = Counter(p.stem.lower() for p in documentos)
    nombres: dict[Path, str] = {}
    usados: set[str] = set()
    for p in documentos:
        base = p.stem if repeticiones[p.stem.lower()] == 1 else f"{p.stem}-{p.suffix.lstrip('.').lower()}"
        nombre, n = base, 2
        while nombre.lower() in usados:  # mismo nombre y extension en otra subcarpeta
            nombre, n = f"{base}-{n}", n + 1
        usados.add(nombre.lower())
        nombres[p] = nombre
    return nombres


# --- Auditoria de la capa de texto de los PDF -------------------------------

AYUDA_AUDITORIA = """Audita una carpeta de PDFs antes de procesarlos, sin abrirlos a mano.

Pensado para corpus grandes que no se pueden revisar uno a uno: marca los
documentos que van a dar problemas para que solo mires esos.

Estados posibles:
  - SIN TEXTO      : ninguna pagina tiene capa de texto (escaneado completo).
                     La extraccion lo devolveria vacio, en silencio.
  - PAGINAS VACIAS : PDF mixto: algunas paginas son imagen. El resto se
                     extrae bien, asi que nada delata lo que falta.
  - ILEGIBLE       : el fichero no se puede abrir.
  - OK             : todas las paginas tienen texto.

Ademas cuenta las "astillas": rectangulos degenerados que duplican un
caracter. El procesador de PDF ya las filtra al extraer; aqui solo se informa.
"""

# Estados de la auditoria que no tiene sentido intentar extraer.
NO_EXTRAIBLES = {"SIN TEXTO", "ILEGIBLE"}

# Altura minima, en puntos, para que una celda de texto de un PDF se considere
# real. La usan la auditoria (para contar astillas) y el procesador de PDF
# (para filtrarlas al extraer).
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

# Caracteres por pagina por debajo de los cuales el PDF es sospechoso.
MINIMO_CHARS_POR_PAGINA = 50


def auditar(ruta: Path, max_paginas: int | None = None) -> dict:
    """Revisa un PDF y devuelve sus metricas y su veredicto.

    'max_paginas' limita la revision a las primeras N paginas.
    """
    fila = {
        "fichero": ruta.name,
        "paginas": 0,
        "chars": 0,
        "astillas": 0,
        "paginas_vacias": 0,
        "estado": "",
        "detalle": "",
    }
    try:
        doc = pdfium.PdfDocument(ruta)
    except Exception as exc:
        fila["estado"] = "ILEGIBLE"
        fila["detalle"] = type(exc).__name__
        return fila

    muestras: list[str] = []
    vacias: list[int] = []
    total = len(doc) if max_paginas is None else min(len(doc), max_paginas)
    for n in range(total):
        try:
            tp = doc[n].get_textpage()
        except Exception:
            continue
        fila["paginas"] += 1
        chars_pagina = tp.count_chars()
        fila["chars"] += chars_pagina
        # Idea tomada de WAC_DataLib (_is_vectorial_page): una pagina con menos
        # de unas decenas de caracteres no tiene texto util, esta escaneada.
        if chars_pagina < MINIMO_CHARS_POR_PAGINA:
            vacias.append(n + 1)
        for i in range(tp.count_rects()):
            x0, y0, x1, y1 = tp.get_rect(i)
            if (y1 - y0) >= ALTURA_MINIMA_CELDA:
                continue
            texto = tp.get_text_bounded(x0, y0, x1, y1)
            if texto.strip():  # solo cuentan las que duplican un caracter real
                fila["astillas"] += 1
                if len(muestras) < 3:
                    muestras.append(f"p.{n + 1}:{texto.strip()[:6]!r}")

    fila["paginas_vacias"] = len(vacias)
    if fila["chars"] == 0:
        fila["estado"] = "SIN TEXTO"
        fila["detalle"] = "escaneado: saldria vacio"
    elif vacias:
        # Lo peligroso de un PDF mixto es que el resto se extrae bien, asi que
        # nada delata que esas paginas se han perdido.
        listado = ",".join(str(p) for p in vacias[:6])
        if len(vacias) > 6:
            listado += f",+{len(vacias) - 6}"
        fila["estado"] = "PAGINAS VACIAS"
        fila["detalle"] = f"{len(vacias)}/{fila['paginas']} sin texto: p.{listado}"
    else:
        fila["estado"] = "OK"
        fila["detalle"] = " ".join(muestras)
    return fila


# Las astillas no van al CSV: ya las filtra el procesador de PDF al extraer, asi que
# son ruido en un informe pensado para decidir que documentos revisar.
COLUMNAS_CSV = ["fichero", "paginas", "chars", "paginas_vacias", "estado", "detalle"]


def escribir_csv(filas: list[dict], destino: Path) -> None:
    """Vuelca el informe, quedandose solo con las columnas accionables."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as fh:
        escritor = csv.DictWriter(fh, fieldnames=COLUMNAS_CSV, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(filas)


def main_auditoria() -> None:
    parser = argparse.ArgumentParser(description=AYUDA_AUDITORIA, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carpeta", type=Path, help="Carpeta con los PDFs a auditar")
    parser.add_argument("--csv", type=Path, help="Guarda el informe completo en un CSV")
    parser.add_argument("--paginas", type=int, default=None, help="Revisa solo las primeras N paginas de cada PDF")
    args = parser.parse_args()

    pdfs = sorted(args.carpeta.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No hay PDFs en {args.carpeta}")

    limite = f" (primeras {args.paginas} paginas)" if args.paginas else ""
    print(f"Auditando {len(pdfs)} PDFs de {args.carpeta}{limite}...\n")
    filas = [auditar(p, args.paginas) for p in pdfs]

    problematicos = [f for f in filas if f["estado"] != "OK"]
    con_astillas = [f for f in filas if f["estado"] == "OK" and f["astillas"]]

    if problematicos:
        print("REVISAR (no se procesaran bien):")
        for f in problematicos:
            print(f"  {f['estado']:11s} {f['detalle']:26s} {f['fichero']}")
    if con_astillas:
        print("\nCon astillas (ya corregidas por el filtro, solo informativo):")
        for f in con_astillas:
            print(f"  {f['astillas']:3d}  {f['detalle']:26s} {f['fichero']}")

    paginas = sum(f["paginas"] for f in filas)
    astillas = sum(f["astillas"] for f in filas)
    print(f"\nResumen: {len(filas)} PDFs, {paginas} paginas")
    print(f"  correctos : {len(filas) - len(problematicos)}")
    print(f"  a revisar : {len(problematicos)}")
    print(f"  astillas  : {astillas} (filtradas al extraer)")

    if args.csv:
        escribir_csv(filas, args.csv)
        print(f"\nInforme completo: {args.csv}")


if __name__ == "__main__":
    main_auditoria()
