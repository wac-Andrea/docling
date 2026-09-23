#!/usr/bin/env python
"""Audita una carpeta de PDFs antes de procesarlos, sin abrirlos a mano.

Pensado para corpus grandes que no se pueden revisar uno a uno: marca los
documentos que van a dar problemas para que solo mires esos.

Detecta:
  - SIN TEXTO   : no tiene capa de texto (escaneado). extraer_pdf.py lo
                  devolveria vacio, en silencio, porque no lleva OCR.
  - POCO TEXTO  : muy pocos caracteres por pagina; suele ser un PDF mixto,
                  con parte del contenido como imagen.
  - ASTILLAS    : rectangulos degenerados que duplican caracteres. El filtro
                  de extraer_pdf.py ya los elimina; aqui solo se informa.
  - ILEGIBLE    : el fichero no se puede abrir.

Uso:
    python auditar_corpus.py carpeta/
    python auditar_corpus.py carpeta/ --csv informe.csv
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
    fila = {"fichero": ruta.name, "paginas": 0, "chars": 0, "astillas": 0, "estado": "", "detalle": ""}
    try:
        doc = pdfium.PdfDocument(ruta)
    except Exception as exc:
        fila["estado"] = "ILEGIBLE"
        fila["detalle"] = type(exc).__name__
        return fila

    muestras: list[str] = []
    total = len(doc) if max_paginas is None else min(len(doc), max_paginas)
    for n in range(total):
        try:
            tp = doc[n].get_textpage()
        except Exception:
            continue
        fila["paginas"] += 1
        fila["chars"] += tp.count_chars()
        for i in range(tp.count_rects()):
            x0, y0, x1, y1 = tp.get_rect(i)
            if (y1 - y0) >= ALTURA_ASTILLA:
                continue
            texto = tp.get_text_bounded(x0, y0, x1, y1)
            if texto.strip():  # solo cuentan las que duplican un caracter real
                fila["astillas"] += 1
                if len(muestras) < 3:
                    muestras.append(f"p.{n + 1}:{texto.strip()[:6]!r}")

    por_pagina = fila["chars"] / fila["paginas"] if fila["paginas"] else 0
    if fila["chars"] == 0:
        fila["estado"] = "SIN TEXTO"
        fila["detalle"] = "escaneado: saldria vacio"
    elif por_pagina < MINIMO_CHARS_POR_PAGINA:
        fila["estado"] = "POCO TEXTO"
        fila["detalle"] = f"{por_pagina:.0f} chars/pagina"
    else:
        fila["estado"] = "OK"
        fila["detalle"] = " ".join(muestras)
    return fila


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
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            escritor = csv.DictWriter(fh, fieldnames=list(filas[0]))
            escritor.writeheader()
            escritor.writerows(filas)
        print(f"\nInforme completo: {args.csv}")


if __name__ == "__main__":
    main()
