"""HTML text and image format validation for local attachments."""
import re
import struct
import zlib
from html.parser import HTMLParser

class _ReadableHTML(HTMLParser):
    """Extract text without rendering HTML, following links, or loading assets."""

    _hidden = {"script", "style", "noscript", "template", "nav", "svg", "canvas", "head"}
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    _blocks = {"address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption", "figure", "footer",
               "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "ol", "p", "pre", "section", "table", "tr", "ul"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        style = re.sub(r"\s+", "", attributes.get("style") or "").lower()
        excluded = tag in self._hidden or "hidden" in attributes or (attributes.get("aria-hidden") or "").lower() == "true"
        excluded = excluded or "display:none" in style or "visibility:hidden" in style
        if self.hidden:
            if tag not in self._void:
                self.hidden.append(tag)
        elif excluded:
            if tag not in self._void:
                self.hidden.append(tag)
        elif tag in self._blocks:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in self._void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            # A mismatched closing tag must not reveal a hidden subtree.
            if tag in self.hidden:
                index = len(self.hidden) - 1 - self.hidden[::-1].index(tag)
                del self.hidden[index:]
            return
        if tag in self._blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)

    def text(self) -> str:
        lines = [re.sub(r"[\t \f\v]+", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG signature does not match the filename")
    offset, dimensions, has_image = 8, None, False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if end > len(data):
            break
        kind, payload = data[offset + 4:offset + 8], data[offset + 8:end - 4]
        checksum = struct.unpack_from(">I", data, end - 4)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != checksum:
            raise ValueError("PNG chunk checksum is invalid")
        if dimensions is None:
            if kind != b"IHDR" or length != 13:
                break
            dimensions = struct.unpack_from(">II", payload)
        elif kind == b"IHDR":
            break
        if kind == b"IDAT" and length:
            has_image = True
        if kind == b"IEND":
            if length == 0 and end == len(data) and dimensions and has_image:
                return dimensions
            break
        offset = end
    raise ValueError("PNG file is truncated or missing image data")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("JPEG signature does not match the filename or file is truncated")
    offset = 2
    frames = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    dimensions = None
    while offset < len(data) - 2:
        if data[offset] != 0xFF:
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack_from(">H", data, offset)[0]
        if length < 2 or offset + length > len(data):
            break
        if marker in frames:
            if length < 8:
                break
            height, width = struct.unpack_from(">HH", data, offset + 3)
            dimensions = width, height
        if marker == 0xDA:
            if dimensions and length >= 6 and offset + length < len(data) - 2:
                return dimensions
            break
        offset += length
    raise ValueError("JPEG file is missing frame dimensions or image data")


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP" or struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("WebP signature does not match the filename or file is truncated")
    offset, dimensions, has_image = 12, None, False
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        length = struct.unpack_from("<I", data, offset + 4)[0]
        start, end = offset + 8, offset + 8 + length
        if end > len(data):
            break
        payload = data[start:end]
        if kind == b"VP8X" and length == 10:
            dimensions = int.from_bytes(payload[4:7], "little") + 1, int.from_bytes(payload[7:10], "little") + 1
        elif kind == b"VP8 " and length >= 10 and payload[3:6] == b"\x9d\x01\x2a":
            frame = struct.unpack_from("<H", payload, 6)[0] & 0x3FFF, struct.unpack_from("<H", payload, 8)[0] & 0x3FFF
            if dimensions and dimensions != frame:
                raise ValueError("WebP frame dimensions do not match its canvas")
            dimensions = frame
            has_image = True
        elif kind == b"VP8L" and length >= 5 and payload[0] == 0x2F:
            packed = int.from_bytes(payload[1:5], "little")
            frame = (packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1
            if dimensions and dimensions != frame:
                raise ValueError("WebP frame dimensions do not match its canvas")
            dimensions = frame
            has_image = True
        elif kind in {b"ANIM", b"ANMF"}:
            raise ValueError("Use a static screenshot rather than an animated WebP")
        offset = end + (length % 2)
    if dimensions and has_image and offset == len(data):
        return dimensions
    raise ValueError("WebP file is truncated or missing image data")


def image_dimensions(suffix: str, data: bytes) -> tuple[int, int]:
    reader = {".png": _png_dimensions, ".jpg": _jpeg_dimensions, ".jpeg": _jpeg_dimensions, ".webp": _webp_dimensions}[suffix]
    width, height = reader(data)
    if not 0 < width <= 20_000 or not 0 < height <= 20_000 or width * height > 40_000_000:
        raise ValueError("Image exceeds the dimension limit")
    return width, height
