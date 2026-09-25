"""Common to all processors: from the DoclingDocument to the outputs.

docling converts any input format -- PDF, Word... -- to the same internal
model, DoclingDocument. What changes between formats is how that model is
obtained (each processor); how it is traversed and what is written is the
same for all of them, and lives here.
"""

from __future__ import annotations

import json
from pathlib import Path

from docling_core.types.doc import (
    DoclingDocument,
    PictureItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)

from ..extractors.images import ImageSaver, picture_block
from ..extractors.tables import get_table_text, is_in_table, table_block
from ..extractors.text import (
    get_document_title,
    get_inline_group,
    get_text,
    join_fragments,
    section_node,
    text_block,
)

# Folder, inside the output folder, where the images of each document go.
IMAGES_FOLDER_NAME = "images"


def build_tree(doc: DoclingDocument, filepath: Path, image_saver: ImageSaver | None = None) -> dict:
    """Traverse the document and turn it into a tree of nested sections.

    'filepath' is the source file, from which the per-page URLs come.
    Level 0 = document; for the other levels, see extractors.text.section_node.
    """
    root = {
        "document": get_document_title(doc),
        "title": doc.name,
        "level": 0,
        "page": None,
        "content": [],
        "subsections": [],
    }
    stack = [root]
    # Split paragraph being rebuilt: (its InlineGroup, its block).
    open_paragraph: tuple[str, dict] | None = None
    # Order of each table and image in the document: their reference when
    # there are no pages.
    n_tables = n_images = 0

    # iterate_items only walks the "body" layer: repeated page headers and
    # footers are left out automatically.
    for item, _ in doc.iterate_items():
        # What hangs from a table is almost all in its 'table' block already
        # (cells, caption). Images are not: the table only leaves the
        # '<!-- image -->' mark for them, so they are kept as their own
        # 'picture' block, right after their table.
        if not isinstance(item, PictureItem) and is_in_table(item, doc):
            continue

        if isinstance(item, (TitleItem, SectionHeaderItem)):
            node = section_node(item)
            # Close the open sections of the same or a deeper level.
            while len(stack) > 1 and stack[-1]["level"] >= node["level"]:
                stack.pop()
            stack[-1]["subsections"].append(node)
            stack.append(node)

        elif isinstance(item, PictureItem):
            n_images += 1
            stack[-1]["content"].append(picture_block(item, doc, filepath, n_images, image_saver))

        elif isinstance(item, TableItem):
            if get_table_text(item, doc):
                n_tables += 1
                stack[-1]["content"].append(table_block(item, doc, filepath, n_tables))

        elif isinstance(item, TextItem):
            text = get_text(item)
            if not text:
                continue

            # The fragments of a split paragraph are joined into a single block.
            group = get_inline_group(item, doc)
            if group is not None and open_paragraph and open_paragraph[0] == group.self_ref:
                block = open_paragraph[1]
                block["text"] = join_fragments(block["text"], text)
                continue

            block = text_block(item, doc, text, group)
            stack[-1]["content"].append(block)
            open_paragraph = (group.self_ref, block) if group is not None else None

    return root


def save_results(doc: DoclingDocument, filepath: Path, output_dir: Path, output_name: str | None = None) -> dict:
    """Write the .md, the .json and the images of a document to 'output_dir'.

    'filepath' is the PDF that was read: the URLs point to it. 'output_name'
    is the name of the output files (by default, the file name without
    extension); the detector changes it if two documents have the same name.

    Returns a summary of what was extracted.
    """
    output_name = output_name or filepath.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"{output_name}.md"
    # Without the class of each image: docling would write it as text ('Logo',
    # 'Photograph') before the image. That information goes in the .json.
    md_path.write_text(doc.export_to_markdown(include_picture_classification=False), encoding="utf-8")

    tree = build_tree(doc, filepath, ImageSaver(output_dir / IMAGES_FOLDER_NAME / output_name))
    json_path = output_dir / f"{output_name}.json"
    json_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "filepath": filepath,
        "tree": tree,
        "md": md_path,
        "json": json_path,
        "images": collect_blocks(tree, "picture"),
        "empty": is_empty(tree),
    }


# --- Queries on an already built tree ---------------------------------------


def collect_blocks(node: dict, block_type: str) -> list[dict]:
    """All the blocks of a type in the tree, in reading order."""
    own = [b for b in node["content"] if b["type"] == block_type]
    return own + [b for child in node["subsections"] for b in collect_blocks(child, block_type)]


def format_outline(node: dict, lines: list[str] | None = None) -> str:
    """Return the outline of the document as indented text."""
    lines = [] if lines is None else lines
    if node["level"] > 0:
        indent = "  " * (node["level"] - 1)
        page = f"  (p. {node['page']})" if node["page"] else ""
        lines.append(f"{indent}{node['title']}{page}")
    for child in node["subsections"]:
        format_outline(child, lines)
    return "\n".join(lines)


def is_empty(tree: dict) -> bool:
    """True if not a single text block was extracted in the whole tree.

    Images do not count: a scanned PDF is image pages, and if they counted it
    would look as if something was extracted when there is no text at all.
    """
    has_text = any(b["type"] != "picture" for b in tree["content"])
    has_heading = tree["level"] > 0 and bool(tree["title"])
    if has_text or has_heading:
        return False
    return all(is_empty(child) for child in tree["subsections"])


def has_sections(node: dict) -> bool:
    """True if the tree has at least one section heading.

    The document title (level 1) does not count: a document with a title but
    no headings still has all its text in a single block.
    """
    return node["level"] >= 2 or any(has_sections(child) for child in node["subsections"])
