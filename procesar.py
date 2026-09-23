#!/usr/bin/env python
"""Proceso completo sobre una carpeta de PDFs: primero audita, luego extrae.

Encadena los dos scripts del proyecto:

  1. auditar_corpus.py  -- revisa la capa de texto de cada PDF y decide cuales
                           no se van a poder extraer.
  2. extraer_pdf.py     -- extrae texto, jerarquia e imagenes de los que si.

Los documentos marcados SIN TEXTO o ILEGIBLE se saltan: extraerlos gastaria
minutos para devolver un fichero vacio. Los de PAGINAS VACIAS si se procesan,
porque su parte digital es aprovechable, pero quedan avisados en el resumen.

Todo -- informe, .md, .json e imagenes -- va a la carpeta de salida.

Uso:
    python procesar.py docs-pruebas -o resultados-pdf
    python procesar.py docs-pruebas -o resultados-pdf --paginas 10 --imagenes
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from auditar_corpus import auditar, escribir_csv
from extraer_pdf import BACKENDS, crear_conversor, procesar_pdf

# Estados del auditor que no tiene sentido intentar extraer.
NO_EXTRAIBLES = {"SIN TEXTO", "ILEGIBLE"}


def parsear_paginas(valor: str) -> tuple[int, int]:
    """Convierte '3' o '1-20' en el rango que espera docling."""
    if "-" in valor:
        inicio, fin = valor.split("-", 1)
        return int(inicio), int(fin)
    return int(valor), int(valor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carpeta", type=Path, help="Carpeta con los PDFs a procesar, o un PDF suelto")
    parser.add_argument("-o", "--salida", type=Path, default=Path("resultados-pdf"), help="Directorio de salida (por defecto: ./resultados-pdf)")
    parser.add_argument("--paginas", type=parsear_paginas, default=(1, 2**31), help="Rango de paginas, p.ej. 1-10")
    parser.add_argument("--backend", choices=list(BACKENDS), default="pypdfium2", help="Lector de la capa de texto")
    parser.add_argument("--imagenes", action="store_true", help="Guarda tambien las imagenes como PNG")
    parser.add_argument("--escala-imagen", type=float, default=2.0, help="Resolucion de las imagenes guardadas")
    args = parser.parse_args()

    # Acepta una carpeta o un PDF suelto, para poder probar un solo documento
    # sin tener que moverlo a un directorio aparte.
    if args.carpeta.is_file():
        pdfs = [args.carpeta]
    else:
        pdfs = sorted(args.carpeta.rglob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"No hay PDFs en {args.carpeta}")

    args.salida.mkdir(parents=True, exist_ok=True)
    tope = args.paginas[1] if args.paginas[1] < 2**31 else None

    # --- Paso 1: auditoria -------------------------------------------------
    print(f"[1/2] Auditando {len(pdfs)} PDFs de {args.carpeta}...")
    filas = [auditar(p, tope) for p in pdfs]
    informe = args.salida / "auditoria.csv"
    escribir_csv(filas, informe)

    por_fichero = {f["fichero"]: f for f in filas}
    descartados = [f for f in filas if f["estado"] in NO_EXTRAIBLES]
    parciales = [f for f in filas if f["estado"] == "PAGINAS VACIAS"]

    for f in descartados:
        print(f"      se salta: {f['estado']:11s} {f['fichero']}")
    print(f"      informe: {informe}")

    # --- Paso 2: extraccion ------------------------------------------------
    extraibles = [p for p in pdfs if por_fichero[p.name]["estado"] not in NO_EXTRAIBLES]
    print(f"\n[2/2] Extrayendo {len(extraibles)} PDFs...")

    # Un solo conversor para todos: cargar los modelos cuesta varios segundos.
    conversor = crear_conversor(args.backend, args.imagenes, args.escala_imagen)

    resultados = []
    for n, pdf in enumerate(extraibles, start=1):
        inicio = time.perf_counter()
        try:
            res = procesar_pdf(pdf, args.salida, conversor, args.paginas, args.imagenes)
        except Exception as exc:  # un PDF roto no debe tumbar toda la tanda
            print(f"      [{n}/{len(extraibles)}] ERROR en {pdf.name}: {type(exc).__name__}: {exc}")
            continue
        resultados.append(res)
        print(
            f"      [{n}/{len(extraibles)}] {pdf.name:32.32s} "
            f"{res['paginas']:3d} pags  {len(res['imagenes']):3d} imgs  "
            f"{time.perf_counter() - inicio:5.1f}s"
        )

    # --- Resumen -----------------------------------------------------------
    paginas = sum(r["paginas"] for r in resultados)
    imagenes = sum(len(r["imagenes"]) for r in resultados)
    print(f"\nListo: {len(resultados)}/{len(pdfs)} documentos, {paginas} paginas, {imagenes} imagenes")
    print(f"Salida: {args.salida}")

    if parciales:
        print("\nAVISO: estos documentos tienen paginas sin texto; su contenido")
        print("se ha perdido en la extraccion porque es imagen:")
        for f in parciales:
            print(f"  {f['fichero']:34.34s} {f['detalle']}")

    vacios = [r for r in resultados if r["vacio"]]
    if vacios:
        print("\nAVISO: no se extrajo texto de:")
        for r in vacios:
            print(f"  {r['pdf'].name}")


if __name__ == "__main__":
    main()
