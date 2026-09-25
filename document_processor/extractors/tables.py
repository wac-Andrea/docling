"""Tables: their content as Markdown, their location and their duplicates."""

from __future__ import annotations

from pathlib import Path

from docling_core.transforms.serializer.markdown import MarkdownDocSerializer, MarkdownParams
from docling_core.types.doc import DoclingDocument, DocItem, TableItem

from .links import get_location


def get_table_text(item: TableItem, doc: DoclingDocument) -> str:
    """The table serialized as Markdown, with its caption on top if it has one.

    Equivalent to item.export_to_markdown(doc), but without the class of the
    images in its cells: the serializer would write it as text ('Logo',
    'Photograph') and it would end up indexed as if it were table content.
    """
    params = MarkdownParams(include_picture_classification=False)
    return MarkdownDocSerializer(doc=doc, params=params).serialize(item=item).text


def is_in_table(item: DocItem, doc: DoclingDocument) -> bool:
    """True if the item's text is already in the Markdown of its table.

    docling hangs from the table items that iterate_items also returns:
      - formatted cells (bold, several paragraphs, lists, nested tables...),
        which it stores twice: as the cell text and as a child item.
      - the table caption, which export_to_markdown puts above the table.
      - the table footnotes, which export_to_markdown does NOT include: those
        are not repeated and must be kept.
    """
    node = item
    while node.parent is not None:
        parent = node.parent.resolve(doc)
        if isinstance(parent, TableItem):
            return node.self_ref not in {ref.cref for ref in parent.footnotes}
        node = parent
    return False


def table_block(item: TableItem, doc: DoclingDocument, filepath: Path, number: int) -> dict:
    """Describe a table: its content as Markdown and where it is.

    'number' is its order among the tables of the document (Table 1, Table 2...).
    """
    page, url, position = get_location(item, filepath)
    block = {
        "type": "table",
        "number": number,
        "page": page,
        "url": url,
        "text": get_table_text(item, doc),
    }
    if position:
        block["position"] = position
    return block
