"""Images: their location, their caption and the saved PNG."""

from __future__ import annotations

import hashlib
from pathlib import Path

from docling_core.types.doc import DoclingDocument, PictureItem

from .links import get_file_url, get_location


class ImageSaver:
    """Saves the detected images and returns the file name of each one.

    Naming convention taken from WAC_DataLib (file2text/extractors/pdf.py):
    'image{page}_{n}.png', so that the files are interchangeable with what its
    extract_images() produces.

    Two identical images share a file -- a repeated logo is not written 40
    times -- but each occurrence keeps its page in the JSON. DataLib's
    filter_unique_images drops duplicates entirely, and with them their
    location, which is precisely the data that matters here.
    """

    def __init__(self, save_dir: Path):
        self.save_dir = save_dir
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self._by_hash: dict[str, str] = {}
        self._counter: dict[int, int] = {}

    def save(self, item: PictureItem, doc: DoclingDocument, page: int | None) -> str | None:
        image = item.get_image(doc)
        if image is None:
            return None

        img_hash = hashlib.md5(image.tobytes()).hexdigest()
        if img_hash in self._by_hash:
            return self._by_hash[img_hash]

        self._counter[page] = self._counter.get(page, 0) + 1
        n_img = self._counter[page]
        # Without pages: running numbering 'image{n}.png', which is how
        # docx2python names images in WAC_DataLib's Word extractor.
        filename = f"image{page}_{n_img}.png" if page is not None else f"image{n_img}.png"
        image.save(self.save_dir / filename)
        self._by_hash[img_hash] = filename
        return filename

    def get_url(self, filename: str | None) -> str | None:
        """Clickable URL of an already saved PNG.

        'filename' keeps just the name, which is WAC_DataLib's convention;
        this is the full path so it can be opened.
        """
        if filename is None:
            return None
        return get_file_url(self.save_dir / filename)


def get_classification(item: PictureItem) -> tuple[str | None, float | None]:
    """Image type according to DocumentFigureClassifier and its confidence (0-1).

    The model spreads the probability over 26 classes (logo, icon, photograph,
    bar_chart, engineering_drawing, screenshot_from_computer...) and returns
    them sorted from highest to lowest. Useful to drop logos and layout
    decorations before indexing. With low confidence (e.g. 'logo' 0.47 versus
    'icon' 0.30) the class is doubtful and it is better not to filter on it alone.
    """
    classification = item.meta.classification if item.meta else None
    if not classification or not classification.predictions:
        return None, None
    best = max(classification.predictions, key=lambda p: p.confidence)
    return best.class_name, round(best.confidence, 3)


def picture_block(
    item: PictureItem,
    doc: DoclingDocument,
    filepath: Path,
    number: int,
    image_saver: ImageSaver | None = None,
) -> dict:
    """Describe an image: where it is, what it is, how big it is and its caption.

    The 'class_name' (logo, photograph, bar_chart...) and the size are useful
    to filter out layout decorations later (bullets, footer logos).

    'number' is its order among the images of the document.
    """
    page, url, position = get_location(item, filepath)
    class_name, confidence = get_classification(item)
    block = {
        "type": "picture",
        "number": number,
        "page": page,
        "url": url,
        "text": (item.caption_text(doc) or "").strip(),
        "class_name": class_name,
        "confidence": confidence,
    }
    if position:
        block["position"] = position
    if image_saver is not None:
        filename = image_saver.save(item, doc, page)
        block["filename"] = filename  # bare name (WAC_DataLib convention)
        block["image_url"] = image_saver.get_url(filename)  # clickable path to the PNG
    return block
