"""Bounded, inert attachment parsing for local chat; no paths or URLs are opened."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import stat
import struct
import unicodedata
import uuid
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree

from .file_formats import _ReadableHTML, image_dimensions


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 8
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARACTERS = 200_000
MAX_DOCX_ENTRIES = 1024
MAX_DOCX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_DOCX_XML_BYTES = 4 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PDF_STREAM_BYTES = 8 * 1024 * 1024
MAX_PDF_TOTAL_STREAM_BYTES = 32 * 1024 * 1024
MAX_PDF_OBJECTS = 5000

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
_TEXT_TYPES = {
    ".txt": "text/plain", ".text": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values", ".json": "application/json",
    ".jsonl": "application/x-ndjson", ".ndjson": "application/x-ndjson", ".xml": "application/xml",
    ".svg": "image/svg+xml", ".html": "text/html", ".htm": "text/html", ".xhtml": "text/html",
}
_CODE_SUFFIXES = {
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".css", ".scss", ".less",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".py", ".pyi", ".r", ".rb", ".go",
    ".rs", ".java", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".swift", ".kt", ".kts",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".sql", ".ipynb", ".env", ".gitignore",
    ".dockerignore", ".tex", ".lua", ".pl", ".php", ".svelte", ".vue", ".properties",
}
_TEXT_NAMES = {"dockerfile", "makefile", "license", "readme", ".env", ".gitignore", ".dockerignore"}
_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_WORD = "{" + _WORD_NAMESPACE + "}"


class AttachmentError(ValueError):
    """An upload cannot be safely represented as chat text or an image."""


def _unicode(value: str, label: str, *, controls: bool = False) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AttachmentError(f"{label} must contain valid Unicode text") from None
    if controls and any(unicodedata.category(char) == "Cc" and char not in "\t\n\r" for char in value):
        raise AttachmentError(f"{label} contains binary control characters")
    return value


def _name(value: Any) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 255 or "/" in value or "\\" in value
            or any(unicodedata.category(char) == "Cc" for char in value)):
        raise AttachmentError("Use a filename label of at most 255 characters, without paths or control characters")
    return _unicode(value.strip(), "Filename")


def _decode(value: Any) -> bytes:
    if not isinstance(value, str) or len(value) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4:
        raise AttachmentError("Each attachment must be at most 10 MiB")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise AttachmentError("Attachment content must be valid base64") from None
    if not data or len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("Each attachment must contain between 1 byte and 10 MiB")
    return data


def preflight_attachment(name: str, content_base64: str) -> tuple[str, int]:
    """Validate labels and reserve decoded bytes without allocating or parsing."""
    name = _name(name)
    if (not isinstance(content_base64, str) or not content_base64 or not content_base64.isascii()
            or len(content_base64) > ((MAX_ATTACHMENT_BYTES + 2) // 3) * 4):
        raise AttachmentError("Each attachment must contain between 1 byte and 10 MiB of valid base64")
    padding = len(content_base64) - len(content_base64.rstrip("="))
    if len(content_base64) % 4 or padding > 2:
        raise AttachmentError("Attachment content must be valid base64")
    size = len(content_base64) // 4 * 3 - padding
    if not 0 < size <= MAX_ATTACHMENT_BYTES:
        raise AttachmentError("Each attachment must contain between 1 byte and 10 MiB")
    return name, size


def _text(value: str) -> str:
    value = _unicode(value, "Extracted text", controls=True).replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        raise AttachmentError("This attachment does not contain readable text")
    if len(value) > MAX_TEXT_CHARACTERS:
        raise AttachmentError(f"Attachment text exceeds {MAX_TEXT_CHARACTERS:,} characters; split the file before uploading")
    return value


def _xml(data: bytes) -> ElementTree.Element:
    if len(data) > MAX_DOCX_XML_BYTES:
        raise AttachmentError("A DOCX XML part exceeds the 4 MiB parsing limit")
    try:
        decoded = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeError:
        raise AttachmentError("DOCX XML parts must use UTF-8 or Unicode UTF-16 encoding") from None
    _unicode(decoded, "DOCX XML", controls=True)
    if "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
        raise AttachmentError("DOCX XML declarations and entities are not supported")
    try:
        return ElementTree.fromstring(decoded)
    except (ElementTree.ParseError, ValueError, RecursionError):
        raise AttachmentError("The DOCX contains invalid XML") from None


def _docx(data: bytes) -> tuple[str, list[str]]:
    # Reject oversized directory tables before ZipFile allocates entry objects.
    footer = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if footer < 0 or footer + 22 > len(data):
        raise AttachmentError("The DOCX is not a valid Word document")
    disk, directory_disk, disk_entries, total_entries = struct.unpack_from("<HHHH", data, footer + 4)
    comment_length = struct.unpack_from("<H", data, footer + 20)[0]
    if disk or directory_disk or disk_entries != total_entries or total_entries > MAX_DOCX_ENTRIES:
        raise AttachmentError("The DOCX exceeds the entry-count limit or uses an unsupported multi-disk archive")
    if footer + 22 + comment_length != len(data):
        raise AttachmentError("The DOCX archive has an invalid directory footer")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError):
        raise AttachmentError("The DOCX is not a valid Word document") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_DOCX_ENTRIES or sum(item.file_size for item in entries) > MAX_DOCX_UNCOMPRESSED_BYTES:
            raise AttachmentError("The DOCX exceeds the entry-count or 20 MiB uncompressed limit")
        names: set[str] = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            lower = entry.filename.lower()
            if (entry.filename in names or path.is_absolute() or ".." in path.parts or "\\" in entry.filename
                    or ":" in entry.filename or (entry.external_attr >> 16) & 0o170000 == stat.S_IFLNK):
                raise AttachmentError("The DOCX contains invalid archive entries")
            names.add(entry.filename)
            if entry.flag_bits & (1 | 64):
                raise AttachmentError("Encrypted DOCX documents are not supported")
            if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise AttachmentError("The DOCX uses an unsupported ZIP compression method")
            if "vbaproject" in lower or lower.endswith((".exe", ".dll", ".com", ".bat", ".cmd", ".js")):
                raise AttachmentError("DOCX macros and embedded executable content are not supported")
        if "word/document.xml" not in names or "[Content_Types].xml" not in names:
            raise AttachmentError("The DOCX is missing its Word document body")

        def read_xml(name: str) -> ElementTree.Element:
            info = archive.getinfo(name)
            if info.file_size > MAX_DOCX_XML_BYTES:
                raise AttachmentError("A DOCX XML part exceeds the 4 MiB parsing limit")
            with archive.open(info) as item:
                raw = item.read(MAX_DOCX_XML_BYTES + 1)
            return _xml(raw)

        try:
            content_types = read_xml("[Content_Types].xml")
            main_type = None
            for node in content_types:
                content_type = node.get("ContentType", "")
                if "macroenabled" in content_type.lower() or "vbaproject" in content_type.lower():
                    raise AttachmentError("Macro-enabled DOCX documents are not supported")
                if node.get("PartName") == "/word/document.xml":
                    main_type = content_type
            if (content_types.tag != "{" + _CONTENT_NAMESPACE + "}Types"
                    or main_type != "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"):
                raise AttachmentError("The upload is not a standard DOCX document")
            external_resources = False
            for name in names:
                if name.lower().endswith(".rels"):
                    relationships = read_xml(name)
                    if relationships.tag != "{" + _REL_NAMESPACE + "}Relationships":
                        raise AttachmentError("The DOCX contains invalid relationships")
                    for relationship in relationships:
                        if relationship.get("TargetMode", "").strip().lower() == "external":
                            external_resources = True
            document = read_xml("word/document.xml")
        except (zipfile.BadZipFile, RuntimeError, OSError, EOFError, zlib.error):
            raise AttachmentError("The DOCX archive is damaged or encrypted") from None
        body = document.find(_WORD + "body")
        if document.tag != _WORD + "document" or body is None:
            raise AttachmentError("The DOCX contains no Word document body")

        def paragraph(node: ElementTree.Element) -> str:
            output = []
            for item in node.iter():
                if item.tag == _WORD + "t":
                    output.append(item.text or "")
                elif item.tag == _WORD + "tab":
                    output.append("\t")
                elif item.tag in {_WORD + "br", _WORD + "cr"}:
                    output.append("\n")
            return "".join(output)

        def blocks(node: ElementTree.Element, depth: int = 0) -> list[str]:
            if depth > 100:
                raise AttachmentError("The DOCX document is nested too deeply")
            output = []
            for child in node:
                if child.tag == _WORD + "p":
                    output.append(paragraph(child))
                elif child.tag == _WORD + "tbl":
                    for row in child.findall(_WORD + "tr"):
                        output.append("\t".join("\n".join(blocks(cell, depth + 1)) for cell in row.findall(_WORD + "tc")))
                else:
                    output.extend(blocks(child, depth + 1))
            return output

        warnings = ["DOCX includes document-body text; layout, images, embedded files, headers, and footers are omitted."]
        if external_resources:
            warnings.append("External links and resources were not loaded.")
        return "\n".join(blocks(body)), warnings


def _pdf(data: bytes) -> tuple[str, int, list[str]]:
    if not data.startswith(b"%PDF-"):
        raise AttachmentError("PDF signature does not match the filename")
    try:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import LimitReachedError
        from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, StreamObject
    except ImportError:
        raise AttachmentError("PDF uploads require pypdf. Install the workbench pdf extra on the computer hosting chat.") from None

    # Context-local pypdf limits also cover font maps, object streams, and forms.
    # Image XObjects are never decoded or OCRed, and external helpers are disabled.
    configuration = {
        "maximum_declared_stream_length": MAX_PDF_STREAM_BYTES,
        "array_based_stream_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "jbig2_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "lzw_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "run_length_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "zlib_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "zlib_maximum_recovery_input_length": 100_000,
        "brotli_maximum_output_length": MAX_PDF_STREAM_BYTES,
        "image_maximum_buffer_size": MAX_PDF_STREAM_BYTES,
        "stream_filters_maximum_length": 4,
        "stream_decoding_work_maximum_length": MAX_PDF_TOTAL_STREAM_BYTES,
        "page_tree_maximum_entries": 2048,
        "page_tree_maximum_depth": 40,
        "xform_maximum_invocations_per_extraction": 200,
        "jbig2dec_binary": None,
        "disable_legacy_handling": True,
    }
    try:
        with apply_configuration(**configuration):
            reader = PdfReader(io.BytesIO(data), strict=True, root_object_recovery_limit=1000)
            if reader.is_encrypted:
                raise AttachmentError("Encrypted PDFs are not supported; upload an unencrypted text PDF")
            count = len(reader.pages)
            if not 0 < count <= MAX_PDF_PAGES:
                raise AttachmentError("PDF uploads must contain between 1 and 100 pages")
            seen: set[tuple[Any, ...]] = set()
            decoded_bytes = 0
            filters = {"/FlateDecode", "/Fl", "/LZWDecode", "/LZW", "/RunLengthDecode", "/RL",
                       "/ASCII85Decode", "/A85", "/ASCIIHexDecode", "/AHx"}

            def inspect(value: Any, depth: int = 0) -> None:
                nonlocal decoded_bytes
                if depth > 40:
                    raise AttachmentError("The PDF resources are nested too deeply")
                if isinstance(value, IndirectObject):
                    key = ("indirect", value.idnum, value.generation)
                    if key in seen:
                        return
                    seen.add(key)
                    value = value.get_object()
                else:
                    key = ("direct", id(value))
                    if key in seen:
                        return
                    seen.add(key)
                if len(seen) > MAX_PDF_OBJECTS:
                    raise AttachmentError("The PDF exceeds the resource-object limit")
                if isinstance(value, StreamObject):
                    if value.get("/Subtype") == "/Image":
                        return
                    if "/F" in value or "/FFilter" in value:
                        raise AttachmentError("PDF streams with external file references are not supported")
                    stream_filters = value.get("/Filter", [])
                    if isinstance(stream_filters, IndirectObject):
                        stream_filters = stream_filters.get_object()
                    stream_filters = stream_filters if isinstance(stream_filters, (list, tuple)) else [stream_filters]
                    if len(stream_filters) > 4 or any(str(item) not in filters for item in stream_filters):
                        raise AttachmentError("The PDF uses an unsupported text-stream filter")
                    raw = value.get_data()
                    decoded_bytes += len(raw)
                    if len(raw) > MAX_PDF_STREAM_BYTES or decoded_bytes > MAX_PDF_TOTAL_STREAM_BYTES:
                        raise AttachmentError("The PDF exceeds the decompressed text-stream limit")
                if isinstance(value, DictionaryObject):
                    for name, child in value.items():
                        if name not in {"/Parent", "/P", "/Metadata", "/Annots"}:
                            inspect(child, depth + 1)
                elif isinstance(value, ArrayObject):
                    for child in value:
                        inspect(child, depth + 1)

            for page in reader.pages:
                inspect(page.get("/Contents"))
                inspect(page.get("/Resources"))
            # Object streams are decoded by the reader while resolving page
            # resources; count these caches against the same aggregate budget.
            object_streams = {item[0] for item in reader.xref_objStm.values()}
            if len(object_streams) > MAX_PDF_OBJECTS:
                raise AttachmentError("The PDF exceeds the resource-object limit")
            for object_id in object_streams:
                inspect(IndirectObject(object_id, 0, reader))
            texts = []
            total = 0
            blank_pages = 0
            for page in reader.pages:
                value = page.extract_text() or ""
                _unicode(value, "PDF text", controls=True)
                total += len(value) + (2 if texts else 0)
                if total > MAX_TEXT_CHARACTERS:
                    raise AttachmentError(f"Attachment text exceeds {MAX_TEXT_CHARACTERS:,} characters; split the PDF before uploading")
                if not value.strip():
                    blank_pages += 1
                texts.append(value)
            text = "\n\n".join(texts)
            if not text.strip():
                raise AttachmentError("This PDF has no extractable text. Scanned/image-only PDFs require OCR, which is not supported")
            warnings = ["PDF text extraction may lose layout and table structure; images are not interpreted and OCR is not performed."]
            if blank_pages:
                warnings.append(f"{blank_pages} page(s) contained no extractable text.")
            return text, count, warnings
    except AttachmentError:
        raise
    except LimitReachedError:
        raise AttachmentError("The PDF exceeds the bounded parsing or decompressed-stream limits") from None
    except Exception:
        raise AttachmentError("The PDF could not be read as a valid text document") from None


def prepare_attachment(name: str, content_base64: str) -> tuple[dict[str, Any], bytes]:
    """Return complete metadata and original bytes for an explicit local upload."""
    name = _name(name)
    data = _decode(content_base64)
    suffix = PurePosixPath(name).suffix.lower()
    record: dict[str, Any] = {
        "id": uuid.uuid4().hex, "name": name, "kind": "text", "media_type": "text/plain",
        "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
    }
    if suffix in _IMAGE_TYPES:
        try:
            width, height = image_dimensions(suffix, data)
        except ValueError as error:
            raise AttachmentError(str(error)) from None
        record.update(kind="image", media_type=_IMAGE_TYPES[suffix], width=width, height=height)
        return record, data
    if suffix == ".docx":
        text, warnings = _docx(data)
        record.update(media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", warnings=warnings)
    elif suffix == ".pdf":
        text, page_count, warnings = _pdf(data)
        record.update(media_type="application/pdf", page_count=page_count, warnings=warnings)
    elif suffix in _TEXT_TYPES or suffix in _CODE_SUFFIXES or name.lower() in _TEXT_NAMES:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise AttachmentError("Text and code attachments must be UTF-8 encoded") from None
        _unicode(text, "Text attachment", controls=True)
        if suffix in {".html", ".htm", ".xhtml"}:
            parser = _ReadableHTML()
            parser.feed(text)
            parser.close()
            text = parser.text()
            record["warnings"] = ["HTML is extracted as inert readable text; scripts, styles, hidden content, and linked resources are omitted."]
        record["media_type"] = _TEXT_TYPES.get(suffix, "text/plain")
    else:
        raise AttachmentError("Unsupported file type. Upload UTF-8 text/code, a text PDF, DOCX, PNG, JPEG, or WebP")
    text = _text(text)
    record.update(text=text, extracted_characters=len(text), preview_text=text[:2000])
    return record, data
