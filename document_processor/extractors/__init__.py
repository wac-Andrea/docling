"""Extractors: each one turns a kind of DoclingDocument item (text, image,
table) into a block of the .json, or computes its links.

They work on docling's internal model, not on the source file, so they are
the same for any input format.
"""
