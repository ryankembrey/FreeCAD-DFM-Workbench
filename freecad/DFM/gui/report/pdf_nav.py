# SPDX-License-Identifier: LGPL-2.1-or-later
# SPDX-FileCopyrightText: 2026 Ryan Kembrey <ryan.FreeCAD@gmail.com>
# SPDX-FileNotice: Part of the DFM addon.

"""Bookmarks and links inside the PDF report.

Qt's PDF writer has no way to link one place in a document to another, nor
to add bookmarks. So the report is written as before, and this module adds
both afterwards as a PDF *incremental update*: new objects and a new
cross-reference table appended to the end of the file. Nothing Qt wrote is
changed; a reader that ignored the update would still see the same pages.

What it adds:

* An outline (the bookmarks panel), opened with the document.
* Link areas: a rectangle on a page that jumps to a place on another page.

Places are given in the layout's own terms: page number (0-based) and a
distance from the top of the page in points. PDF counts up from the bottom;
the conversion is done here.

Only the standard library is used, and only the structure Qt writes is
relied on: a classic cross-reference table, one /Pages node listing every
page, and an indirect /Annots array on each page.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

Place = tuple[int, float]  # (page, distance from the top in points)


@dataclass
class Bookmark:
    title: str
    place: Place
    children: list["Bookmark"] = field(default_factory=list)


@dataclass
class Link:
    page: int
    x: float  # from the left, in points
    y: float  # from the top, in points
    w: float
    h: float
    target: Place


@dataclass
class Navigation:
    bookmarks: list[Bookmark] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.bookmarks or self.links)


class PdfNavError(RuntimeError):
    pass


def _find_object(data: bytes, num: int) -> tuple[int, int]:
    """(start, end) of the body of object *num*: from after ``obj`` to ``endobj``.
    The last definition wins, as in a PDF reader."""
    found = None
    for m in re.finditer(rb"(?<![0-9])%d\s+0\s+obj\b" % num, data):
        end = data.find(b"endobj", m.end())
        if end < 0:
            break
        found = (m.end(), end)
    if found is None:
        raise PdfNavError(f"object {num} not found")
    return found


def _object(data: bytes, num: int) -> bytes:
    start, end = _find_object(data, num)
    return data[start:end].strip()


def _ref(body: bytes, key: bytes) -> Optional[int]:
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+0\s+R", body)
    return int(m.group(1)) if m else None


def _trailer(data: bytes) -> tuple[bytes, int]:
    """The last trailer dictionary and the offset of the last cross-reference table."""
    pos = data.rfind(b"trailer")
    sx = data.rfind(b"startxref")
    if pos < 0 or sx < 0:
        raise PdfNavError("no trailer (a cross-reference stream PDF is not supported)")
    start = data.find(b"<<", pos)
    end = data.rfind(b">>", start, sx)
    m = re.search(rb"startxref\s+(\d+)", data[sx:])
    if start < 0 or end < 0 or not m:
        raise PdfNavError("unreadable trailer")
    return data[start:end + 2], int(m.group(1))


def _text(s: str) -> bytes:
    """A PDF text string: plain when it is printable ASCII, else UTF-16 with a byte order mark."""
    if all(32 <= ord(c) < 127 for c in s):
        return b"(" + s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii") + b")"
    return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def _num(v: float) -> bytes:
    return (b"%.2f" % v).rstrip(b"0").rstrip(b".") or b"0"


def add_navigation(path: str, nav: Navigation) -> None:
    """Append *nav*'s bookmarks and links to the PDF at *path*, in place.

    The file is written to a temporary file next to it and swapped in, so
    a failure leaves the original untouched.
    """
    if not nav:
        return
    with open(path, "rb") as fh:
        data = fh.read()
    update = navigation_update(data, nav)
    tmp = path + ".nav"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.write(update)
    os.replace(tmp, path)


def navigation_update(data: bytes, nav: Navigation) -> bytes:
    """The bytes to append to the PDF *data* to add *nav*."""
    trailer, prev_xref = _trailer(data)
    size_m = re.search(rb"/Size\s+(\d+)", trailer)
    root = _ref(trailer, b"Root")
    if not size_m or root is None:
        raise PdfNavError("trailer without /Size or /Root")
    size = int(size_m.group(1))
    catalog = _object(data, root)
    pages_num = _ref(catalog, b"Pages")
    if pages_num is None:
        raise PdfNavError("catalog without /Pages")
    pages_body = _object(data, pages_num)
    kids_m = re.search(rb"/Kids\s*\[(.*?)\]", pages_body, re.S)
    if not kids_m:
        raise PdfNavError("no /Kids")
    page_nums = [int(n) for n in re.findall(rb"(\d+)\s+0\s+R", kids_m.group(1))]
    heights = []
    annots_of = []
    for num in page_nums:
        body = _object(data, num)
        if b"/Kids" in body:
            raise PdfNavError("nested page tree is not supported")
        box = re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", body)
        heights.append((float(box.group(2)), float(box.group(4))) if box else (0.0, 842.0))
        annots_of.append(_ref(body, b"Annots"))

    objects: dict[int, bytes] = {}
    next_num = size

    def new(body: bytes) -> int:
        nonlocal next_num
        num = next_num
        next_num += 1
        objects[num] = body
        return num

    def dest(place: Place) -> bytes:
        page, top = place
        page = min(max(int(page), 0), len(page_nums) - 1)
        y0, y1 = heights[page]
        y = max(y0, min(y1, y1 - top))
        return b"[%d 0 R /XYZ null %s null]" % (page_nums[page], _num(y))

    # Links, added to each page's /Annots array.
    new_annots: dict[int, list[int]] = {}
    for link in nav.links:
        if not 0 <= link.page < len(page_nums):
            continue
        _y0, y1 = heights[link.page]
        x0, x1 = link.x, link.x + link.w
        top, bottom = y1 - link.y, y1 - link.y - link.h
        num = new(b"<< /Type /Annot /Subtype /Link /Rect [%s %s %s %s] /Border [0 0 0] /Dest %s >>"
                  % (_num(x0), _num(bottom), _num(x1), _num(top), dest(link.target)))
        new_annots.setdefault(link.page, []).append(num)
    for page, nums in new_annots.items():
        refs = b" ".join(b"%d 0 R" % n for n in nums)
        annots = annots_of[page]
        if annots is not None:
            old = _object(data, annots)
            inner = old.strip()[1:-1].strip() if old.strip().startswith(b"[") else b""
            objects[annots] = b"[" + (inner + b" " if inner else b"") + refs + b"]"
        else:
            body = _object(data, page_nums[page])
            objects[page_nums[page]] = body[:body.rfind(b">>")] + b"/Annots [" + refs + b"]\n>>"

    # The outline.
    outline_num = None
    if nav.bookmarks:
        outline_num = new(b"")  # filled in below

        def add_items(items: list[Bookmark], parent: int) -> tuple[int, int, int]:
            """Make *items* under *parent*; (first, last, visible descendants)."""
            nums = [new(b"") for _ in items]
            count = 0
            for i, (item, num) in enumerate(zip(items, nums)):
                parts = [b"/Title " + _text(item.title), b"/Parent %d 0 R" % parent,
                         b"/Dest " + dest(item.place)]
                if i:
                    parts.append(b"/Prev %d 0 R" % nums[i - 1])
                if i + 1 < len(nums):
                    parts.append(b"/Next %d 0 R" % nums[i + 1])
                if item.children:
                    first, last, sub = add_items(item.children, num)
                    parts += [b"/First %d 0 R" % first, b"/Last %d 0 R" % last, b"/Count %d" % sub]
                    count += sub
                objects[num] = b"<< " + b" ".join(parts) + b" >>"
                count += 1
            return nums[0], nums[-1], count

        first, last, count = add_items(nav.bookmarks, outline_num)
        objects[outline_num] = b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (first, last, count)
        cat = re.sub(rb"/Outlines\s+\d+\s+0\s+R", b"", catalog)
        cat = re.sub(rb"/PageMode\s*/\w+", b"", cat)
        cat = cat[:cat.rfind(b">>")] + b"/Outlines %d 0 R\n/PageMode /UseOutlines\n>>" % outline_num
        objects[root] = cat

    # Append: the objects, a cross-reference table for them, and a trailer.
    out = bytearray(b"\n" if not data.endswith(b"\n") else b"")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(data) + len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"
    xref_at = len(data) + len(out)
    # Object 0, the head of the free list, opens the table as in a full one;
    # some readers expect every table to start there.
    out += b"xref\n0 1\n0000000000 65535 f \n"
    for num in sorted(objects):
        out += b"%d 1\n%010d 00000 n \n" % (num, offsets[num])
    rest = re.sub(rb"/(Size|Prev|Root)\s+\d+(\s+0\s+R)?", b"", trailer[2:-2]).strip()
    out += (b"trailer\n<< /Size %d /Root %d 0 R /Prev %d " % (max(next_num, size), root, prev_xref)
            + rest + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref_at)
    return bytes(out)
