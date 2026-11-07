"""Inert attachment extraction and bounded malformed-file handling."""

from __future__ import annotations

import base64
import builtins
import hashlib
import io
import json
import struct
import unittest
import zipfile
from unittest.mock import patch

from orchestrator.chat import attachments
from orchestrator.chat.attachments import AttachmentError, prepare_attachment
import zlib

try:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject
except ImportError:
    PdfWriter = None


def png(width=1, height=1):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0")) + chunk(b"IEND", b""))


def jpeg():
    frame = b"\x08\x00\x02\x00\x03\x01\x01\x11\x00"
    scan = b"\x01\x01\x00\x00\x3f\x00"
    return b"\xff\xd8\xff\xc0" + struct.pack(">H", len(frame) + 2) + frame + b"\xff\xda" + struct.pack(">H", len(scan) + 2) + scan + b"\0\xff\xd9"


def webp():
    image = b"\x2f" + ((3 - 1) | ((2 - 1) << 14)).to_bytes(4, "little") + b"\0"
    payload = b"WEBPVP8L" + struct.pack("<I", len(image)) + image
    return b"RIFF" + struct.pack("<I", len(payload)) + payload


def encoded(value: bytes | str) -> str:
    return base64.b64encode(value.encode("utf-8") if isinstance(value, str) else value).decode("ascii")


def docx(document: str | None = None, extra: dict[str, bytes | str] | None = None) -> bytes:
    document = document or '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Project summary</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Budget</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>$40</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      </w:body></w:document>'''
    values = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "word/document.xml": document,
        "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    }
    values.update(extra or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in values.items():
            archive.writestr(name, value)
    return stream.getvalue()


def pdf(text: str | None = "Hello from a text PDF", *, pages: int = 1, encrypted: bool = False,
        stream_bytes: bytes | None = None, compressed: bool = False, resource=None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        if resource:
            page[NameObject("/Resources")].update(resource)
        if text is not None or stream_bytes is not None:
            content = DecodedStreamObject()
            content.set_data(stream_bytes if stream_bytes is not None else f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode())
            if compressed:
                content = content.flate_encode()
            page[NameObject("/Contents")] = writer._add_object(content)
    if encrypted:
        writer.encrypt("synthetic-test-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class AttachmentTextTests(unittest.TestCase):
    def test_metadata_original_bytes_and_full_text_are_retained(self):
        raw = "\ufeff  def example():\r\n    return 'hello'\r\n".encode()
        record, stored = prepare_attachment("example.py", encoded(raw))
        self.assertEqual(stored, raw)
        self.assertRegex(record["id"], r"^[0-9a-f]{32}$")
        self.assertEqual(record["name"], "example.py")
        self.assertEqual(record["kind"], "text")
        self.assertEqual(record["text"], "  def example():\n    return 'hello'\n")
        self.assertEqual(record["size_bytes"], len(raw))
        self.assertEqual(record["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record["extracted_characters"], len(record["text"]))
        self.assertEqual(record["preview_text"], record["text"])
        json.dumps(record, ensure_ascii=False).encode("utf-8")

    def test_common_text_code_and_data_names_are_supported(self):
        for name in ("notes.txt", "README", "Dockerfile", "Makefile", ".env", "data.json", "records.csv", "config.yaml",
                     "main.tsx", "query.sql", "app.swift", "settings.toml", "file.xml", "diagram.svg"):
            with self.subTest(name=name):
                record, _ = prepare_attachment(name, encoded("Readable content"))
                self.assertEqual(record["kind"], "text")
                self.assertEqual(record["text"], "Readable content")

    def test_preview_limit_does_not_truncate_model_text(self):
        value = "x" * 3000
        record, _ = prepare_attachment("long.md", encoded(value))
        self.assertEqual(record["text"], value)
        self.assertEqual(len(record["preview_text"]), 2000)
        self.assertEqual(record["extracted_characters"], 3000)

    def test_html_is_inert_and_omits_scripts_hidden_content_and_link_fetches(self):
        value = '''<html><head><script>secret</script></head><body><h1>Visible &amp; clear</h1>
         <p>Read this.</p><p hidden>Hidden</p><div style="display:none">Invisible</div>
         <script>fetch('http://example.invalid')</script><style>Secret style</style>
         <img src="http://example.invalid/image.png"><a href="file:///secret">A link label</a></body></html>'''
        with patch("socket.socket", side_effect=AssertionError("Network is forbidden")), patch("builtins.open", side_effect=AssertionError("File reads are forbidden")):
            record, _ = prepare_attachment("page.html", encoded(value))
        self.assertIn("Visible & clear", record["text"])
        self.assertIn("A link label", record["text"])
        for hidden in ("secret", "Hidden", "Invisible", "fetch", "Secret style", "example.invalid"):
            self.assertNotIn(hidden, record["text"])

    def test_invalid_names_are_rejected(self):
        for name in ("../a.txt", "C:\\a.txt", "/a.txt", "bad\n.txt", "bad\x7f.txt", "bad\x85.txt", "bad\ud800.txt", "x" * 256, "", None):
            with self.subTest(name=repr(name)), self.assertRaises(AttachmentError):
                prepare_attachment(name, encoded("text"))

    def test_invalid_binary_unknown_empty_or_oversized_text_is_rejected(self):
        for name, data in (("bad.txt", b'\xff'), ("bad.txt", b'zero\x00byte'), ("bad.txt", "control\x80".encode()),
                           ("empty.txt", b'  \n'), ("empty.txt", b''), ("app.exe", b'MZbinary'),
                           ("big.txt", b'x' * (attachments.MAX_TEXT_CHARACTERS + 1))):
            with self.subTest(name=name, size=len(data)), self.assertRaises(AttachmentError):
                prepare_attachment(name, encoded(data))

    def test_base64_preflight_and_decoded_size_are_bounded(self):
        with patch("orchestrator.chat.attachments.MAX_ATTACHMENT_BYTES", 3):
            with patch("orchestrator.chat.attachments.base64.b64decode", side_effect=AssertionError("Must reject before decode")):
                with self.assertRaisesRegex(AttachmentError, "10 MiB"):
                    prepare_attachment("big.txt", "AAAAA")
            with self.assertRaises(AttachmentError):
                prepare_attachment("big.txt", encoded(b'abcd'))
        for value in ("!!", "data:text/plain;base64,YQ==", "é", None):
            with self.subTest(value=value), self.assertRaises(AttachmentError):
                prepare_attachment("bad.txt", value)

    def test_pdf_dependency_absence_has_install_instruction(self):
        original = builtins.__import__

        def without_pypdf(name, *args, **kwargs):
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("Synthetic missing optional dependency")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_pypdf):
            with self.assertRaisesRegex(AttachmentError, "pdf extra"):
                prepare_attachment("document.pdf", encoded(b'%PDF-1.4\nfixture'))


class AttachmentImageTests(unittest.TestCase):
    def test_images_keep_original_bytes_and_validated_dimensions(self):
        for name, raw, dimensions, media_type in (("image.png", png(), (1, 1), "image/png"),
                                                 ("image.jpg", jpeg(), (3, 2), "image/jpeg"),
                                                 ("image.webp", webp(), (3, 2), "image/webp")):
            with self.subTest(name=name):
                record, stored = prepare_attachment(name, encoded(raw))
                self.assertEqual(stored, raw)
                self.assertEqual(record["kind"], "image")
                self.assertEqual(record["media_type"], media_type)
                self.assertEqual((record["width"], record["height"]), dimensions)
                self.assertNotIn("text", record)

    def test_image_damage_signature_mismatch_and_dimension_bombs_fail(self):
        broken = bytearray(png())
        broken[45] ^= 1
        for name, raw in (("bad.png", b'not an image'), ("bad.jpg", png()), ("bad.png", bytes(broken)),
                          ("bad.png", png(20001, 1)), ("bad.png", png(10000, 10000)), ("bad.webp", webp()[:-1])):
            with self.subTest(name=name), self.assertRaises(AttachmentError):
                prepare_attachment(name, encoded(raw))


class AttachmentDocxTests(unittest.TestCase):
    def test_body_paragraph_and_table_cells_are_extracted(self):
        raw = docx()
        record, stored = prepare_attachment("notes.docx", encoded(raw))
        self.assertEqual(stored, raw)
        self.assertEqual(record["text"], "Project summary\nBudget\t$40")
        self.assertIn("wordprocessingml.document", record["media_type"])
        self.assertTrue(record["warnings"])

    def test_broken_or_non_word_archives_are_rejected(self):
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, "w") as archive:
            archive.writestr("other.txt", "not Word")
        for raw in (b'not zip', docx()[:-12], empty.getvalue(), docx("<invalid>")):
            with self.subTest(size=len(raw)), self.assertRaises(AttachmentError):
                prepare_attachment("bad.docx", encoded(raw))

    def test_corrupt_deflate_payload_has_a_safe_attachment_error(self):
        raw = bytearray(docx())
        filename_length, extra_length = struct.unpack_from('<HH', raw, 26)
        raw[30 + filename_length + extra_length] = 0x06  # Invalid DEFLATE block type.
        with self.assertRaisesRegex(AttachmentError, "damaged"):
            prepare_attachment("damaged.docx", encoded(raw))

    def test_macros_and_archive_paths_are_rejected(self):
        extra_values = [
            {"word/vbaProject.bin": b'binary'},
            {"../outside.txt": "No traversal"},
            {"[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.macroEnabled.main+xml"/></Types>'},
        ]
        for extra in extra_values:
            with self.subTest(extra=list(extra)), self.assertRaises(AttachmentError):
                prepare_attachment("unsafe.docx", encoded(docx(extra=extra)))

    def test_hyperlinks_and_embedded_files_are_ignored_without_fetching_or_opening(self):
        document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p>
         <w:hyperlink><w:r><w:t>Download the specification</w:t></w:r></w:hyperlink>
         </w:p></w:body></w:document>'''
        extra = {
            "word/_rels/document.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="x" TargetMode="External" Target="https://example.invalid/spec"/></Relationships>',
            "word/embeddings/spreadsheet.bin": b'Embedded content is never opened or interpreted',
        }
        raw = docx(document, extra)
        with patch("socket.socket", side_effect=AssertionError("No network allowed")), patch("builtins.open", side_effect=AssertionError("No external files allowed")), patch("subprocess.Popen", side_effect=AssertionError("No process allowed")):
            record, _ = prepare_attachment("linked.docx", encoded(raw))
        self.assertEqual(record["text"], "Download the specification")
        self.assertIn("External links and resources were not loaded.", record["warnings"])

    def test_entity_declarations_are_rejected_in_utf8_and_utf16(self):
        xml = '<!DOCTYPE root [<!ENTITY secret "expanded">]><root>&secret;</root>'
        for raw in (docx(xml), docx(extra={"word/document.xml": xml.encode("utf-16")})):
            with self.subTest(size=len(raw)), self.assertRaises(AttachmentError):
                prepare_attachment("unsafe.docx", encoded(raw))

    def test_archive_and_xml_bounds_are_enforced_without_silent_truncation(self):
        with patch("orchestrator.chat.attachments.MAX_DOCX_UNCOMPRESSED_BYTES", 100):
            with self.assertRaisesRegex(AttachmentError, "uncompressed"):
                prepare_attachment("large.docx", encoded(docx()))
        with patch("orchestrator.chat.attachments.MAX_DOCX_XML_BYTES", 100):
            with self.assertRaisesRegex(AttachmentError, "XML part"):
                prepare_attachment("large.docx", encoded(docx()))
        with patch("orchestrator.chat.attachments.MAX_DOCX_ENTRIES", 2):
            with self.assertRaisesRegex(AttachmentError, "entry-count"):
                prepare_attachment("large.docx", encoded(docx()))
        with patch("orchestrator.chat.attachments.MAX_TEXT_CHARACTERS", 10):
            with self.assertRaisesRegex(AttachmentError, "split"):
                prepare_attachment("large.docx", encoded(docx()))

    def test_encrypted_archive_flag_is_rejected_before_reading(self):
        raw = bytearray(docx())
        cursor = 0
        while True:
            cursor = raw.find(b'PK\x01\x02', cursor)
            if cursor < 0:
                break
            flags = struct.unpack_from('<H', raw, cursor + 8)[0]
            struct.pack_into('<H', raw, cursor + 8, flags | 1)
            cursor += 4
        with self.assertRaisesRegex(AttachmentError, "Encrypted"):
            prepare_attachment("encrypted.docx", encoded(raw))


@unittest.skipIf(PdfWriter is None, "Optional PDF dependency pypdf is not installed")
class AttachmentPdfTests(unittest.TestCase):
    def test_text_pdf_is_extracted_with_page_count_and_warnings(self):
        raw = pdf(pages=2, compressed=True)
        record, stored = prepare_attachment("report.pdf", encoded(raw))
        self.assertEqual(stored, raw)
        self.assertEqual(record["page_count"], 2)
        self.assertEqual(record["text"].count("Hello from a text PDF"), 2)
        self.assertEqual(record["media_type"], "application/pdf")
        self.assertIn("OCR", record["warnings"][0])

    def test_encrypted_blank_and_invalid_pdf_are_rejected(self):
        for raw, message in ((pdf(encrypted=True), "Encrypted"), (pdf(text=None), "OCR"),
                             (b'%PDF-1.4\ninvalid', "valid text"), (b'not PDF', "signature")):
            with self.subTest(message=message), self.assertRaisesRegex(AttachmentError, message):
                prepare_attachment("bad.pdf", encoded(raw))

    def test_image_only_pdf_is_rejected_without_ocr_or_image_decoding(self):
        writer = PdfWriter()
        page = writer.add_blank_page(width=100, height=100)
        image = DecodedStreamObject()
        image.set_data(b'\xff\0\0')
        image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                      NameObject("/Width"): NumberObject(1), NameObject("/Height"): NumberObject(1),
                      NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8)})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(image)})})
        content = DecodedStreamObject()
        content.set_data(b'q 100 0 0 100 0 0 cm /Im0 Do Q')
        page[NameObject("/Contents")] = writer._add_object(content)
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(AttachmentError, "image-only.*OCR"):
            prepare_attachment("scan.pdf", encoded(output.getvalue()))

    def test_page_and_extracted_text_limits_are_enforced(self):
        with self.assertRaisesRegex(AttachmentError, "100 pages"):
            prepare_attachment("many.pdf", encoded(pdf(pages=101)))
        with patch("orchestrator.chat.attachments.MAX_TEXT_CHARACTERS", 10):
            with self.assertRaisesRegex(AttachmentError, "split"):
                prepare_attachment("long.pdf", encoded(pdf()))

    def test_compressed_stream_limit_applies_before_text_extraction(self):
        raw = pdf(stream_bytes=b' ' * 4096, compressed=True)
        with patch("orchestrator.chat.attachments.MAX_PDF_STREAM_BYTES", 1024):
            with self.assertRaisesRegex(AttachmentError, "bounded parsing|decompressed"):
                prepare_attachment("bomb.pdf", encoded(raw))

    def test_aggregate_decoded_stream_limit_is_enforced(self):
        with patch("orchestrator.chat.attachments.MAX_PDF_TOTAL_STREAM_BYTES", 90):
            with self.assertRaisesRegex(AttachmentError, "decompressed"):
                prepare_attachment("large.pdf", encoded(pdf(pages=3)))

    def test_pdf_configuration_is_context_local_and_never_calls_helpers(self):
        from pypdf import get_configuration
        before = get_configuration()
        with patch("socket.socket", side_effect=AssertionError("Network is forbidden")), patch("subprocess.Popen", side_effect=AssertionError("External processes are forbidden")):
            prepare_attachment("safe.pdf", encoded(pdf(compressed=True)))
        self.assertIs(get_configuration(), before)


if __name__ == "__main__":
    unittest.main()
