"""Encrypted evidence and fail-closed scan adapter (no public media routes). Revisions: Git."""

import hashlib
import re
import socket
import struct
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile
from zlib import error as ZlibError

from cryptography.fernet import Fernet, InvalidToken
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.translation import gettext as _
from pypdf import PdfReader
from pypdf.errors import PyPdfError

from .models import Document, DocumentBlob

MIME = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


class UploadLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.content_type == 'multipart/form-data':
            try:
                size = int(request.META.get('CONTENT_LENGTH') or 0)
            except ValueError:
                size = 0
            if not 0 < size <= settings.DOCUMENT_REQUEST_LIMIT:
                response = HttpResponse(_('This upload exceeds the permitted request size.'), status=413)
                response.headers['Cache-Control'] = 'no-store, private'
                return response
        return self.get_response(request)


@dataclass(frozen=True, slots=True)
class Upload:
    name: str
    media_type: str
    data: bytes
    checksum: str
    scan: str
    method: str


def cipher():
    try:
        return Fernet(settings.DOCUMENT_KEY.encode('ascii'))
    except (ValueError, TypeError) as exc:
        raise ValidationError(_('Private document storage is not configured. Contact ICT.')) from exc


def _safe_pdf(raw):
    if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-2048:]:
        return False
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted:
        return False
    root = reader.root_object
    names = root['/Names'] if '/Names' in root else {}
    blocked = ('/AA', '/OpenAction', '/AcroForm', '/AF', '/XFA')
    if any(key in root for key in blocked) or any(
        key in names for key in ('/JavaScript', '/EmbeddedFiles')
    ):
        return False
    pages = root['/Pages']
    if not 1 <= pages.get('/Count', 0) <= 500:
        return False
    # Static policy is intentionally narrower than the full PDF format.
    return not any(
        key in raw for key in (b'/JavaScript', b'/EmbeddedFile', b'/Launch', b'/RichMedia', b'/XFA')
    )


def _safe_office(raw, extension):
    word = extension == '.docx'
    part = 'word/document.xml' if word else 'xl/workbook.xml'
    type_name = 'wordprocessingml.document' if word else 'spreadsheetml.sheet'
    with ZipFile(BytesIO(raw)) as bundle:
        entries = bundle.infolist()
        if not 2 <= len(entries) <= 256 or sum(item.file_size for item in entries) > 25 * 1024 * 1024:
            return False
        names = set()
        for item in entries:
            path = item.filename.lower()
            if (
                path in names
                or path.startswith('/')
                or ':' in path
                or '\\' in path
                or '..' in PurePosixPath(path).parts
                or item.flag_bits & 1
                or item.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
                or item.file_size > 200 * max(1, item.compress_size) + 1024
                or any(
                    chunk in path
                    for chunk in ('vbaproject', 'activex', 'embeddings/', 'externallinks/', 'customui/')
                )
            ):
                return False
            names.add(path)
        if part not in names or '[content_types].xml' not in names:
            return False
        manifest = ElementTree.fromstring(bundle.read('[Content_Types].xml'))
        if not any(
            item.get('PartName') == f'/{part}'
            and item.get('ContentType')
            == f'application/vnd.openxmlformats-officedocument.{type_name}.main+xml'
            for item in manifest
        ):
            return False
        ElementTree.fromstring(bundle.read(part))
        for name in bundle.namelist():
            if name.lower().endswith('.rels'):
                if any(
                    rel.get('TargetMode', '').lower() == 'external'
                    for rel in ElementTree.fromstring(bundle.read(name))
                ):
                    return False
        # Decompress every entry within the aggregate limit to verify integrity.
        return bundle.testzip() is None


def scan_bytes(raw):
    if not settings.CLAMD_SOCKET:
        if settings.ENV == 'test' or (settings.ENV == 'development' and settings.DEMO_MODE):
            return Document.Scan.CLEAN, 'synthetic-structure'
        return Document.Scan.PENDING, 'not-configured'
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(2.0)
            conn.connect(settings.CLAMD_SOCKET)
            conn.sendall(b'zINSTREAM\x00')
            for start in range(0, len(raw), 65536):
                chunk = raw[start : start + 65536]
                conn.sendall(struct.pack('!I', len(chunk)) + chunk)
            conn.sendall(b'\x00' * 4)
            verdict = bytearray()
            while len(verdict) < 1024 and b'\x00' not in verdict:
                packet = conn.recv(256)
                if not packet:
                    break
                verdict.extend(packet)
        report, terminator, _rest = verdict.partition(b'\x00')
        if not terminator:
            return Document.Scan.PENDING, 'scanner-unavailable'
        if report == b'stream: OK':
            return Document.Scan.CLEAN, 'clamav'
        if report.startswith(b'stream: ') and report.endswith(b' FOUND'):
            return Document.Scan.BLOCKED, 'clamav'
    except (OSError, AttributeError):
        pass
    return Document.Scan.PENDING, 'scanner-unavailable'


def prepare_upload(upload):
    # Names are display-only, not file paths or authority to serve browser content.
    name = unicodedata.normalize('NFC', upload.name.replace('\\', '/').split('/')[-1]).strip()
    extension = PurePosixPath(name).suffix.lower()
    if len(name) > 120 or re.search(r'[\x00-\x1f\x7f]', name) or extension not in MIME:
        raise ValidationError(_('Use a PDF, DOCX or XLSX file with a valid name.'))
    if not 0 < upload.size <= settings.DOCUMENT_FILE_LIMIT:
        raise ValidationError(_('Each document must be between 1 byte and 5 MB.'))
    cipher()
    raw = upload.read(settings.DOCUMENT_FILE_LIMIT + 1)
    if len(raw) != upload.size:
        raise ValidationError(_('The uploaded file could not be read completely.'))
    try:
        valid = _safe_pdf(raw) if extension == '.pdf' else _safe_office(raw, extension)
    except (
        PyPdfError,
        BadZipFile,
        DefusedXmlException,
        ElementTree.ParseError,
        OSError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
        RuntimeError,
        ZlibError,
        struct.error,
    ):
        valid = False
    if not valid:
        raise ValidationError(
            _('The document is malformed, encrypted or contains unsupported active content.')
        )
    scan, method = scan_bytes(raw)
    if scan == Document.Scan.BLOCKED:
        raise ValidationError(_('The scanner blocked this document. Contact ICT for assistance.'))
    return Upload(name, MIME[extension], raw, hashlib.sha256(raw).hexdigest(), scan, method)


def store(case, actor, prepared, kind):
    if case.documents.count() >= 30:
        raise ValidationError(_('This request has reached its 30-document limit.'))
    encrypted = cipher().encrypt(prepared.data)
    doc = Document.objects.create(
        case=case,
        uploaded_by=actor,
        kind=kind,
        name=prepared.name,
        media_type=prepared.media_type,
        byte_size=len(prepared.data),
        checksum=prepared.checksum,
        scan=prepared.scan,
        scan_method=prepared.method,
    )
    DocumentBlob.objects.create(document=doc, ciphertext=encrypted)
    return doc


def read_blob(doc):
    try:
        data = cipher().decrypt(bytes(doc.blob.ciphertext))
    except (InvalidToken, DocumentBlob.DoesNotExist) as exc:
        raise ValidationError(_('This private document cannot be verified. Contact ICT.')) from exc
    if len(data) != doc.byte_size or hashlib.sha256(data).hexdigest() != doc.checksum:
        raise ValidationError(_('This private document cannot be verified. Contact ICT.'))
    return data
