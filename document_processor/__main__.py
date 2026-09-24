"""Proceso completo sobre una carpeta de documentos PDF y Word.

  1. detector.py comprueba de que tipo es cada fichero, por su contenido y no
     solo por la extension, y audita la capa de texto de los PDF. Los PDF
     SIN TEXTO o ILEGIBLE se saltan: extraerlos gastaria minutos para devolver
     un fichero vacio. Los de PAGINAS VACIAS si se procesan, pero quedan
     avisados en el resumen.
  2. Cada documento va a su procesador (processors/): los PDF por
     pdf_processor, los Word (.docx, .doc, .rtf) por docx_processor.
  3. De todos se extrae el texto con su jerarquia, las imagenes (guardadas
     como PNG) y las tablas, cada una con su referencia en el original
     (extractors/).

Todo -- informe, .md, .json e imagenes -- va a la carpeta de salida. Con un
documento suelto se muestra ademas su esquema por consola.

Uso:
    python -m document_processor docs-pruebas
    python -m document_processor docs-pruebas -o resultados --paginas 1-10
    python -m document_processor docs-pruebas/acta.docx
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .detector import NO_EXTRAIBLES, auditar, escribir_csv, listar_documentos, nombres_de_salida, tipo_de
from .processors.base import formatear_indice, recopilar, tiene_secciones
from .processors.docx_processor import crear_conversor as crear_conversor_word
from .processors.docx_processor import procesar_word
from .processors.pdf_processor import BACKENDS, TODAS_LAS_PAGINAS, crear_conversor, parsear_paginas, procesar_pdf

SALIDA_POR_DEFECTO = Path(__file__).resolve().parent / "output"


def mostrar_detalle(res: dict) -> None:
    """Esquema, imagenes y tablas de un documento, cuando se procesa uno solo."""
    print("\n--- Estructura del documento ---")
    print(formatear_indice(res["arbol"]) or "(el documento no tiene encabezados detectados)")

    for tipo, titulo in (("picture", "Imagenes"), ("table", "Tablas")):
        bloques = recopilar(res["arbol"], tipo)
        if not bloques:
            continue
        print(f"\n--- {titulo}: {len(bloques)} ---")
        for b in bloques:
            pagina = f"p.{b['pagina']:<4}" if b["pagina"] else "      "
            pos = b.get("posicion")
            medidas = f"{pos['ancho']}x{pos['alto']} pt" if pos else ""
            referencia = b.get("archivo") or b["url"] or "(sin pagina: un Word no tiene)"
            clase = f"{b['clase']} ({b['confianza']:.0%})" if b.get("clase") else ""
            print(f"  {b['numero']:>3}. {pagina} {medidas:<16} {clase:<26} {referencia}")

    print(f"\nGenerado:\n  {res['md']}\n  {res['json']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m document_processor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("carpeta", type=Path, help="Carpeta con los documentos a procesar, o un documento suelto")
    parser.add_argument("-o", "--salida", type=Path, default=SALIDA_POR_DEFECTO, help="Directorio de salida (por defecto: document_processor/output)")
    parser.add_argument("--paginas", type=parsear_paginas, default=TODAS_LAS_PAGINAS, help="Rango de paginas de los PDF, p.ej. 1-10 (a los Word no les afecta)")
    parser.add_argument("--backend", choices=list(BACKENDS), default="pypdfium2", help="Lector de la capa de texto de los PDF")
    parser.add_argument("--escala-imagen", type=float, default=2.0, help="Resolucion de las imagenes guardadas de los PDF")
    args = parser.parse_args()

    if not args.carpeta.exists():
        raise SystemExit(f"No encuentro: {args.carpeta}")
    candidatos = listar_documentos(args.carpeta)
    if not candidatos:
        raise SystemExit(f"No hay documentos PDF, .docx, .doc ni .rtf en {args.carpeta}")

    args.salida.mkdir(parents=True, exist_ok=True)

    # --- Paso 1: tipo de cada documento -----------------------------------
    print(f"[1/3] Comprobando {len(candidatos)} documentos de {args.carpeta}...")
    tipos = {p: tipo_de(p) for p in candidatos}
    for p, tipo in tipos.items():
        if tipo is None:
            print(f"      se salta: el contenido no es un {p.suffix.lower()} valido: {p.name}")
    pdfs = [p for p in candidatos if tipos[p] == "pdf"]
    words = [p for p in candidatos if tipos[p] == "word"]
    print(f"      {len(pdfs)} PDF, {len(words)} Word")

    # --- Paso 2: auditoria de los PDF -------------------------------------
    parciales = []
    descartados: set[Path] = set()
    if pdfs:
        tope = args.paginas[1] if args.paginas[1] < TODAS_LAS_PAGINAS[1] else None
        print(f"\n[2/3] Auditando la capa de texto de {len(pdfs)} PDF...")
        filas = [auditar(p, tope) for p in pdfs]
        informe = args.salida / "auditoria.csv"
        escribir_csv(filas, informe)

        for pdf, fila in zip(pdfs, filas):
            if fila["estado"] in NO_EXTRAIBLES:
                descartados.add(pdf)
                print(f"      se salta: {fila['estado']:11s} {fila['fichero']}")
            elif fila["estado"] == "PAGINAS VACIAS":
                parciales.append(fila)
        print(f"      informe: {informe}")
    else:
        print("\n[2/3] Sin PDF: no hay nada que auditar")

    # --- Paso 3: extraccion -----------------------------------------------
    extraibles = sorted(p for p in pdfs + words if p not in descartados)
    nombres = nombres_de_salida(extraibles)
    print(f"\n[3/3] Extrayendo {len(extraibles)} documentos...")

    # Un solo conversor por formato: cargar los modelos cuesta varios segundos.
    conversor_pdf = crear_conversor(args.backend, args.escala_imagen) if pdfs else None
    conversor_word = crear_conversor_word() if words else None

    resultados = []
    for n, doc in enumerate(extraibles, start=1):
        inicio = time.perf_counter()
        try:
            if tipos[doc] == "pdf":
                res = procesar_pdf(doc, args.salida, conversor_pdf, args.paginas, nombres[doc])
            else:
                res = procesar_word(doc, args.salida, conversor_word, nombres[doc])
        except Exception as exc:  # un documento roto no debe tumbar toda la tanda
            print(f"      [{n}/{len(extraibles)}] ERROR en {doc.name}: {type(exc).__name__}: {exc}")
            continue
        resultados.append(res)
        paginas = f"{res['paginas']:3d} pags" if "paginas" in res else "   - pags"
        origen = f"  (convertido de {res['convertido']})" if res.get("convertido") else ""
        print(
            f"      [{n}/{len(extraibles)}] {doc.name:32.32s} {paginas}  "
            f"{len(res['imagenes']):3d} imgs  {len(recopilar(res['arbol'], 'table')):3d} tablas  "
            f"{time.perf_counter() - inicio:5.1f}s{origen}"
        )

    if len(candidatos) == 1 and resultados:
        mostrar_detalle(resultados[0])

    # --- Resumen -----------------------------------------------------------
    imagenes = sum(len(r["imagenes"]) for r in resultados)
    tablas = sum(len(recopilar(r["arbol"], "table")) for r in resultados)
    print(f"\nListo: {len(resultados)}/{len(candidatos)} documentos, {imagenes} imagenes, {tablas} tablas")
    print(f"Salida: {args.salida}")

    if parciales:
        print("\nAVISO: estos PDF tienen paginas sin texto; su contenido")
        print("se ha perdido en la extraccion porque es imagen:")
        for f in parciales:
            print(f"  {f['fichero']:34.34s} {f['detalle']}")

    vacios = [r for r in resultados if r["vacio"]]
    if vacios:
        print("\nAVISO: no se extrajo texto de:")
        for r in vacios:
            print(f"  {r['documento'].name}")

    # En un Word los titulos salen de los estilos (Titulo 1, Titulo 2...): si
    # el documento los simula con negrita o tamano a mano, el texto se extrae
    # entero pero sin secciones. En un PDF, el modelo de layout no los vio.
    sin_secciones = [r for r in resultados if not r["vacio"] and not tiene_secciones(r["arbol"])]
    if sin_secciones:
        print("\nAVISO: estos documentos no tienen titulos detectados; el texto")
        print("esta completo, pero en el .json todo cuelga de la raiz, sin secciones:")
        for r in sin_secciones:
            print(f"  {r['documento'].name}")


if __name__ == "__main__":
    main()
