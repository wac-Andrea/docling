"""Links and location of each item in the source document.

Every URL in the .json comes from here:
  - 'url'       : opens the source document at the item's page.
  - 'image_url' : path to the saved PNG of an image.
And also the 'position' (box in points) of images and tables.
"""

from __future__ import annotations

from pathlib import Path

from docling_core.types.doc import DocItem


def get_file_url(filepath: Path) -> str:
    """Clickable file:// URL of a local file."""
    return filepath.resolve().as_uri()


def get_page_url(filepath: Path, page: int | None) -> str | None:
    """URL that opens the document directly at that page.

    The '#page=N' fragment is a standard PDF open parameter: browsers and
    Acrobat understand it, so the link is clickable. Formats without pages
    arrive with page None and have no URL.
    """
    if page is None:
        return None
    return f"{get_file_url(filepath)}#page={page}"


def get_location(item: DocItem, filepath: Path) -> tuple[int | None, str | None, dict | None]:
    """Where the item is in the source: page, link to it and box.

    Only formats with pages (PDF) have this information. Otherwise the three
    values are None, and the only reference is the 'number' that build_tree
    assigns.
    """
    prov = item.prov[0] if item.prov else None
    if prov is None:
        return None, None, None
    box = prov.bbox
    position = {
        "x": round(box.l, 1),
        "y": round(box.b, 1),
        "width": round(abs(box.r - box.l), 1),
        "height": round(abs(box.t - box.b), 1),
    }
    return prov.page_no, get_page_url(filepath, prov.page_no), position
