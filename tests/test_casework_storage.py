"""Synthetic document format and scanner tests; no live daemon or unsafe files. Revisions: Git."""

from io import BytesIO
from unittest.mock import MagicMock, patch
from zipfile import ZIP_DEFLATED, ZipFile

from cryptography.fernet import Fernet
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from casework.models import Document
from casework.storage import MIME, prepare_upload, scan_bytes


def document_fixture(ext, *, external=False):
    path, package, body = (
        ('word/document.xml', 'wordprocessingml.document', '<document/>')
        if ext == '.docx'
        else ('xl/workbook.xml', 'spreadsheetml.sheet', '<workbook/>')
    )
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr(path, body)
        archive.writestr(
            '[Content_Types].xml',
            f'<Types><Override PartName="/{path}" ContentType="'
            f'application/vnd.openxmlformats-officedocument.{package}.main+xml"/></Types>',
        )
        if external:
            archive.writestr(
                '_rels/.rels', '<Relationships><Relationship TargetMode="External" /></Relationships>'
            )
    return SimpleUploadedFile('evidence' + ext, output.getvalue())


@override_settings(DOCUMENT_KEY=Fernet.generate_key().decode(), CLAMD_SOCKET='')
class StorageTests(SimpleTestCase):
    def test_allowed_office_types_use_server_media_type_and_reject_external_relationships(self):
        for extension in ('.docx', '.xlsx'):
            with self.subTest(extension=extension):
                result = prepare_upload(document_fixture(extension))
                self.assertEqual(result.media_type, MIME[extension])
                self.assertEqual(len(result.checksum), 64)
                with self.assertRaises(ValidationError):
                    prepare_upload(document_fixture(extension, external=True))

    @override_settings(ENV='staging', DEMO_MODE=False)
    def test_without_scanner_non_demo_never_gets_a_clean_verdict(self):
        self.assertEqual(scan_bytes(b'synthetic bytes'), (Document.Scan.PENDING, 'not-configured'))

    @override_settings(CLAMD_SOCKET='/synthetic/path.sock')
    def test_scanner_adapter_recognises_only_complete_expected_verdicts(self):
        for chunks, expected in (
            ([b'stream: O', b'K\x00'], Document.Scan.CLEAN),
            ([b'stream: Synthetic FOUND\x00'], Document.Scan.BLOCKED),
            ([b'stream: OK', b''], Document.Scan.PENDING),
            ([b'stream: limits exceeded ERROR\x00'], Document.Scan.PENDING),
        ):
            with self.subTest(chunks=chunks), patch('casework.storage.socket.socket') as socket:
                conn = MagicMock()
                conn.recv.side_effect = chunks
                socket.return_value.__enter__.return_value = conn
                verdict, _method = scan_bytes(b'fictional evidence')
                self.assertEqual(verdict, expected)
                self.assertEqual(conn.sendall.call_args_list[0].args[0], b'zINSTREAM\x00')
