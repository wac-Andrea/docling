"""Text: section headings, paragraphs and list items."""

from __future__ import annotations

from docling_core.types.doc import (
    DoclingDocument,
    DocItem,
    InlineGroup,
    ListGroup,
    ListItem,
    SectionHeaderItem,
    TextItem,
    TitleItem,
)


def get_page(item: DocItem) -> int | None:
    """Page number where the item appears (None if it has no provenance)."""
    prov = getattr(item, "prov", None)
    return prov[0].page_no if prov else None


def get_text(item: DocItem) -> str:
    """Text of the item, stripped of surrounding whitespace."""
    return (getattr(item, "text", "") or "").strip()


def section_node(item: DocItem) -> dict:
    """Tree node for a title or section heading.

    Level 1 = document title, 2+ = section headings. The depth comes from the
    'level' docling assigns: in a PDF, its layout model and heading hierarchy
    stage decide it; in a Word, the document's heading styles.
    """
    level = 1 if isinstance(item, TitleItem) else item.level + 1
    return {
        "title": get_text(item),
        "level": level,
        "page": get_page(item),
        "content": [],
        "subsections": [],
    }


def get_document_title(doc: DoclingDocument) -> str | None:
    """Title of the document, or None if none is recognized.

    If docling marks a title (a Word with the 'Title' style), that one. In a
    PDF the layout model does not tell it apart: it comes out as one more
    heading, so the first heading is taken if it is on the first page. The
    file metadata is useless: it is usually empty or holds things like
    'Diapositiva 1' or 'Microsoft Word - informe.doc'.
    """
    heading = None
    for item, _ in doc.iterate_items():
        if isinstance(item, TitleItem) and get_text(item):
            return get_text(item)
        if heading is None and isinstance(item, SectionHeaderItem) and get_text(item):
            heading = item
    if heading is not None and get_page(heading) == 1:
        return get_text(heading)
    return None


def get_inline_group(item: DocItem, doc: DoclingDocument) -> InlineGroup | None:
    """Paragraph the item belongs to, if it is a fragment of a split one.

    docling splits paragraphs with mixed formatting (a bold word, a link...)
    into fragments so as not to lose that formatting: each fragment is a
    separate item, and all of them hang from the same InlineGroup.
    """
    parent = item.parent.resolve(doc) if item.parent else None
    return parent if isinstance(parent, InlineGroup) else None


# Punctuation attached to the previous or next fragment, with no space.
NO_SPACE_BEFORE = tuple(",.;:)]}»”?!%…")
NO_SPACE_AFTER = tuple("([{«“¿¡")


def join_fragments(previous: str, following: str) -> str:
    """Join two fragments of a split paragraph.

    docling stores each fragment without its surrounding whitespace, so there
    is no knowing whether the original had one between them. One is added,
    except next to punctuation: 'extranjeros' + ', con el fin' must not give
    'extranjeros , con el fin'.
    """
    if following.startswith(NO_SPACE_BEFORE) or previous.endswith(NO_SPACE_AFTER):
        return previous + following
    return f"{previous} {following}"


def get_list_level(list_item: ListItem, doc: DoclingDocument) -> int:
    """Depth of the list item: 1 = main list, 2 = sublist, etc.

    Each list is a ListGroup, and a sublist hangs from the item that contains
    it, so counting the ListGroups above is enough. With it the parent of each
    item can be rebuilt, just like with the 'level' of sections: the parent
    of an item is the last previous item one level up.
    """
    level, parent = 0, list_item.parent
    while parent is not None:
        node = parent.resolve(doc)
        if isinstance(node, ListGroup):
            level += 1
        parent = node.parent
    return max(level, 1)


def text_block(item: TextItem, doc: DoclingDocument, text: str, group: InlineGroup | None) -> dict:
    """Block for a paragraph or a list item.

    'group' is the InlineGroup if 'item' is the first fragment of a split
    paragraph; build_tree appends the following fragments.
    """
    # List item the text belongs to: the item itself or, for a split
    # paragraph, the ListItem the fragments hang from (it arrives empty: its
    # text is in them).
    list_item = item if isinstance(item, ListItem) else None
    if group is not None and isinstance(group.parent.resolve(doc), ListItem):
        list_item = group.parent.resolve(doc)

    if list_item is not None:
        return {
            "type": "list_item",
            "level": get_list_level(list_item, doc),
            "marker": (list_item.marker or "").strip(),
            "page": get_page(item),
            "text": text,
        }
    return {"type": item.label.value, "page": get_page(item), "text": text}  # text, caption...
