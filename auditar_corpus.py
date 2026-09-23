#!/usr/bin/env python
"""Audita una carpeta de PDFs antes de procesarlos, sin abrirlos a mano.

Pensado para corpus grandes que no se pueden revisar uno a uno: marca los
documentos que van a dar problemas para que solo mires esos.

Estados posibles:
  - SIN TEXTO      : ninguna pagina tiene capa de texto (escaneado completo).
                     extraer_pdf.py lo devolveria vacio, en silencio.
  - PAGINAS VACIAS : PDF mixto: algunas paginas son imagen. El resto se
                     extrae bien, asi que nada delata lo que falta.
  - ILEGIBLE       : el fichero no se puede abrir.
  - OK             : todas las paginas tienen texto.

Ademas cuenta las "astillas": rectangulos degenerados que duplican un
caracter. extraer_pdf.py ya las filtra al extraer; aqui solo se informa.

Uso:
    python auditar_corpus.py carpeta/
    python auditar_corpus.py carpeta/ --paginas 10 --csv informe.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import pypdfium2 as pdfium

# Ver ALTURA_MINIMA_CELDA en extraer_pdf.py: por debajo de esto un rectangulo
# no contiene letra propia y solo duplica la del vecino.
ALTURA_ASTILLA = 0.1

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
            if (y1 - y0) >= ALTURA_ASTILLA:
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


# Las astillas no van al CSV: ya las filtra extraer_pdf.py al extraer, asi que
# son ruido en un informe pensado para decidir que documentos revisar.
COLUMNAS_CSV = ["fichero", "paginas", "chars", "paginas_vacias", "estado", "detalle"]


def escribir_csv(filas: list[dict], destino: Path) -> None:
    """Vuelca el informe, quedandose solo con las columnas accionables."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", newline="", encoding="utf-8") as fh:
        escritor = csv.DictWriter(fh, fieldnames=COLUMNAS_CSV, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(filas)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
    main()
