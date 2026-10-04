"""EPUB: translate every text block of every XHTML file, keep structure,
images, links and styles, and switch the book to Persian + right-to-left."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile

from lxml import etree

from .base import Document, is_translatable

XHTML_NS = "http://www.w3.org/1999/xhtml"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "blockquote",
         "figcaption", "caption", "td", "th", "div", "section", "article", "aside",
         "header", "footer", "pre", "address", "summary", "title"}
SKIP = {"script", "style", "code", "math", "svg", "head"}
MEDIA = {"img", "svg", "image", "audio", "video", "object", "iframe"}


def _local(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _has_block_child(el) -> bool:
    for d in el.iterdescendants():
        if _local(d.tag) in BLOCK:
            return True
    return False


def _in_skip(el) -> bool:
    p = el.getparent()
    while p is not None:
        if _local(p.tag) in SKIP:
            return True
        p = p.getparent()
    return False


def _norm(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]*\n[ \t\r\f\v]*", "\n", re.sub(r"[ \t\r\f\v]+", " ", text)).strip()


def _text_of(el) -> str:
    parts = []
    def walk(e):
        if e.text:
            parts.append(e.text)
        for c in e:
            if _local(c.tag) == "br":
                parts.append("\n")
            elif _local(c.tag) not in SKIP and _local(c.tag) not in MEDIA:
                walk(c)
            if c.tail:
                parts.append(c.tail)
    walk(el)
    return _norm("".join(parts))


def _target_of(el):
    """Descend into a single wrapper (li>a, p>span) so links/styles survive."""
    t = el
    while True:
        kids = [c for c in t if isinstance(c.tag, str)]
        if (len(kids) == 1 and not (t.text or "").strip() and not (kids[0].tail or "").strip()
                and _local(kids[0].tag) not in MEDIA and _local(kids[0].tag) not in SKIP
                and _local(kids[0].tag) != "br"):
            t = kids[0]
        else:
            return t


def _replace(el, text: str):
    media = [c for c in el.iterdescendants() if _local(c.tag) in MEDIA
             and not any(_local(a.tag) in MEDIA for a in c.iterancestors() if a is not el)]
    for c in list(el):
        el.remove(c)
    lines = text.split("\n")
    el.text = lines[0]
    ns = el.tag.split("}")[0] + "}" if el.tag.startswith("{") else ""
    for line in lines[1:]:
        br = etree.SubElement(el, ns + "br")
        br.tail = line
    for m in media:
        m.tail = None
        el.append(m)


class _Html:
    def __init__(self, name, tree, blocks):
        self.name, self.tree, self.blocks = name, tree, blocks  # blocks: [(elem, seg_id)]


def load_epub(data: bytes) -> Document:
    zin = zipfile.ZipFile(io.BytesIO(data))
    names = zin.namelist()
    container = etree.fromstring(zin.read("META-INF/container.xml"))
    opf_path = container.xpath("//*[local-name()='rootfile']/@full-path")[0]
    opf_dir = posixpath.dirname(opf_path)
    opf = etree.fromstring(zin.read(opf_path))
    html_names, ncx_name = [], None
    for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
        mt = item.get("media-type", "")
        href = posixpath.normpath(posixpath.join(opf_dir, item.get("href", "")))
        from urllib.parse import unquote
        href = unquote(href)
        if href not in names:
            continue
        if mt in ("application/xhtml+xml", "text/html"):
            html_names.append(href)
        elif mt == "application/x-dtbncx+xml":
            ncx_name = href

    parser = etree.XMLParser(recover=True, resolve_entities=False, remove_blank_text=False)
    segments: list[str] = []
    htmls: list[_Html] = []
    for name in html_names:
        try:
            tree = etree.fromstring(zin.read(name), parser)
        except Exception:
            continue
        if tree is None:
            continue
        blocks = []
        for el in tree.iter():
            tag = _local(el.tag)
            if tag not in BLOCK:
                continue
            if _in_skip(el) and tag != "title":
                continue
            if _has_block_child(el):
                # Text loose inside a container (e.g. <div>text<p>..</p></div>) stays as is.
                continue
            txt = _text_of(el)
            if not is_translatable(txt):
                continue
            blocks.append((el, len(segments)))
            segments.append(txt)
        htmls.append(_Html(name, tree, blocks))

    ncx_tree, ncx_blocks = None, []
    if ncx_name:
        try:
            ncx_tree = etree.fromstring(zin.read(ncx_name), parser)
            for el in ncx_tree.xpath("//*[local-name()='text']"):
                t = _norm(el.text or "")
                if is_translatable(t):
                    ncx_blocks.append((el, len(segments)))
                    segments.append(t)
        except Exception:
            ncx_tree = None

    def build(tr: dict) -> bytes:
        out = io.BytesIO()
        zout = zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED)
        zout.writestr(zipfile.ZipInfo("mimetype"), b"application/epub+zip",
                      compress_type=zipfile.ZIP_STORED)
        done = {"mimetype"}
        for h in htmls:
            for el, sid in h.blocks:
                if sid in tr:
                    _replace(_target_of(el), tr[sid])
            root = h.tree
            root.set("dir", "rtl")
            root.set("lang", "fa")
            root.set(XML_LANG, "fa")
            for body in root.xpath("//*[local-name()='body']"):
                body.set("dir", "rtl")
            zout.writestr(h.name, etree.tostring(root, xml_declaration=True, encoding="utf-8"))
            done.add(h.name)
        if ncx_tree is not None:
            for el, sid in ncx_blocks:
                if sid in tr:
                    el.text = tr[sid]
            zout.writestr(ncx_name, etree.tostring(ncx_tree, xml_declaration=True, encoding="utf-8"))
            done.add(ncx_name)
        # Book metadata: language fa, right-to-left page progression
        for lang in opf.xpath("//*[local-name()='metadata']/*[local-name()='language']"):
            lang.text = "fa"
        for spine in opf.xpath("//*[local-name()='spine']"):
            spine.set("page-progression-direction", "rtl")
        zout.writestr(opf_path, etree.tostring(opf, xml_declaration=True, encoding="utf-8"))
        done.add(opf_path)
        for info in zin.infolist():
            if info.filename not in done:
                zout.writestr(info, zin.read(info.filename))
        zout.close()
        return out.getvalue()

    return Document(segments, build, ".epub")
