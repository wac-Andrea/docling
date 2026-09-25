# docling

Extracción de texto con jerarquía (encabezados, listas, tablas e imágenes) de
documentos PDF y Word, basada en [docling](https://github.com/docling-project/docling).
Sin OCR: solo se procesa la capa de texto nativa de los documentos.

## Uso

Todo se ejecuta desde la raíz del proyecto con `python -m document_processor`,
pasándole una carpeta (se recorren también las subcarpetas) o un documento
suelto. Admite `.pdf`, `.docx`, `.doc` y `.rtf` mezclados:

```bash
python -m document_processor docs-pruebas
python -m document_processor docs-pruebas -o resultados
python -m document_processor docs-pruebas/acta.docx
```

Con un documento suelto muestra además su esquema, sus imágenes y sus tablas
por consola.

Antes de extraer comprueba el tipo real de cada fichero por su contenido, no
solo por la extensión, y lo manda al procesador que le toca. Por cada documento
genera en la carpeta de salida (por defecto `document_processor/output/`, que
git ignora):

- `<nombre>.md`: el texto en Markdown.
- `<nombre>.json`: el árbol de secciones con textos, listas, tablas e imágenes.
- `imagenes/<nombre>/`: cada imagen como PNG.
- `auditoria.csv`: el estado de la capa de texto de cada PDF.

La raíz del `.json` lleva en `document` el título del documento, o `null` si
no se reconoce: en Word, el párrafo con el estilo Título; en PDF, el primer
encabezado si está en la primera página. `titulo` es el nombre del fichero.

En el `.json`, cada imagen y cada tabla lleva su `numero` de orden en el
documento. En los PDF llevan además `pagina`, `url` (abre el PDF en esa página)
y `posicion`; las imágenes, también `url_imagen` con la ruta a su PNG. Los Word
no tienen páginas, así que ahí `pagina` y `url` salen a `null`.

Los niveles de las secciones salen, en Word, de los estilos de título del
documento (Título 1, Título 2…). En PDF los asigna la etapa de jerarquía de
títulos de docling, a partir de los marcadores del PDF, la numeración
(`8.` › `8.1` › `8.1.1` › `a.`) y el tamaño de letra. Acierta sobre todo con
títulos numerados; cuando se mezclan numerados y sin numerar, puede colgar un
apartado de otro que en realidad es su hermano.

Cada imagen, de PDF o de Word, lleva además `clase` y `confianza` (0-1), del
modelo `DocumentFigureClassifier` de docling: `logo`, `icon`, `photograph`,
`bar_chart`, `flow_chart`, `engineering_drawing`… (26 clases). Sirve para
descartar logos y adornos de maquetación antes de indexar en un RAG. Con
confianza baja la clase es dudosa: conviene no filtrar solo por ella.

Si un PDF y un Word se llaman igual (`informe.pdf` e `informe.docx`), sus
salidas se distinguen por la extensión: `informe-pdf.json` e `informe-docx.json`.

Opciones (solo afectan a los PDF): `--paginas 1-10` para limitar el rango,
`--backend` para cambiar el lector de la capa de texto y `--escala-imagen` para
la resolución de los PNG.

La auditoría de los PDF también se puede lanzar sola, sin extraer nada:

```bash
python -m document_processor.detector docs-pruebas --paginas 10 --csv informe.csv
```

## Estructura

```
document_processor/
    __main__.py            comando principal: detecta, audita y reparte cada documento
    detector.py            tipo real de cada fichero, nombres de salida y auditoría de PDF

    processors/            uno por formato: obtienen el DoclingDocument
        base.py            lo común: árbol de secciones y escritura de .md, .json e imágenes
        pdf_processor.py   capa de texto nativa, filtro de astillas, niveles de títulos, rango de páginas
        docx_processor.py  .docx directo; .doc y .rtf convertidos antes a .docx

    extractors/            uno por tipo de elemento: lo convierten en bloque del .json
        text.py            títulos, párrafos (y párrafos partidos) y listas con nivel y marcador
        images.py          bloques de imagen, su clase (logo, foto...) y guardado de PNG
        tables.py          bloques de tabla, sin duplicar el texto de sus celdas
        links.py           URLs y localización: página, #page=N, posición, rutas a PNG

    output/                salida por defecto: <documento>.json, .md, imagenes/
```

docling convierte PDF y Word al mismo modelo interno (`DoclingDocument`), así
que los extractores y `processors/base.py` valen para cualquier formato: un
formato nuevo solo necesitaría su propio procesador.

## Requisitos

### Python

- Python 3.10 o superior (probado con 3.13).
- Paquetes:

  ```bash
  pip install docling
  ```

  `docling` ya incluye `pypdfium2` y `python-docx`, que usan los scripts.

### Modelos de docling

Los modelos (layout y tablas de los PDF, clasificador de imágenes) se descargan
de HuggingFace la primera vez que se usan y quedan en caché. En un servidor o
en Docker conviene descargarlos al construir la imagen, para no depender de
internet al procesar:

```dockerfile
RUN docling-tools models download layout tableformer picture_classifier
```

### Conversión de `.doc` (Word 97-2003) y `.rtf`

`docx_processor.py` solo extrae `.docx`. Los `.doc` y `.rtf` se convierten antes
a `.docx`, y para eso hace falta **uno** de estos dos programas en la máquina
donde se ejecuta el script:

| Programa | Dónde se usa | Notas |
|---|---|---|
| **LibreOffice** | Servidor / Docker (recomendado) | Se usa siempre que esté instalado. |
| **Microsoft Word** | Solo Windows, desarrollo en local | Se usa si no hay LibreOffice. Necesita además `pip install pywin32`. |

Sin ninguno de los dos, los `.docx` se procesan con normalidad y cada `.doc`
o `.rtf` se salta con el error `no se puede convertir a .docx: instala
LibreOffice o Microsoft Word`.

LibreOffice también lo aprovecha docling, si está disponible, para convertir en
imagen los gráficos EMF/WMF y los SmartArt de los `.docx`. Sin él, esas
imágenes pueden quedar vacías; el texto no se ve afectado.

#### Instalar LibreOffice

- **Docker** (imagen basada en Debian/Ubuntu, p. ej. `python:3.13-slim`):

  ```dockerfile
  RUN apt-get update \
   && apt-get install -y --no-install-recommends libreoffice-writer \
   && rm -rf /var/lib/apt/lists/*
  ```

  Basta con `libreoffice-writer`; no hace falta la suite completa.

- **Linux**: `sudo apt-get install -y libreoffice-writer`
- **Mac**: `brew install --cask libreoffice`
- **Windows**: instalador de [libreoffice.org](https://www.libreoffice.org/download/).
  No hace falta añadirlo al PATH: el script lo busca también en
  `C:\Program Files\LibreOffice`.

> **Conversiones en paralelo.** LibreOffice no admite dos conversiones a la vez
> con el mismo perfil de usuario. Hoy los documentos se convierten de uno en
> uno, así que no hay problema; si el servicio llega a atender varias peticiones
> en paralelo, cada conversión necesitará su propio perfil
> (`-env:UserInstallation=file:///tmp/lo-<id>`).
