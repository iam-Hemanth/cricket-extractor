import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('rebuild', Path(__file__).parents[1] / 'rebuild.py')
rebuild = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rebuild)


class ArchiveGuards(unittest.TestCase):
    def make_archive(self, root, members):
        archive = root / 'input.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name, body, kind in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                if kind == 'link':
                    info.type = tarfile.SYMTYPE
                    info.linkname = '/tmp/untrusted'
                    tar.addfile(info)
                else:
                    tar.addfile(info, io.BytesIO(body))
        return archive

    def test_authenticated_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.make_archive(root, [('matches.jsonl', b'one\n', 'file')])
            rebuild.unpack_archive(archive, root / 'out', {'matches.jsonl': hashlib.sha256(b'one\n').hexdigest()}, {'matches.jsonl': 4})
            self.assertEqual((root / 'out/matches.jsonl').read_bytes(), b'one\n')

    def test_traversal_and_links_and_unexpected_files_rejected(self):
        for name, kind in [('../escape', 'file'), ('matches.jsonl', 'link'), ('unexpected', 'file')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = self.make_archive(root, [(name, b'x', kind)])
                with self.assertRaises(ValueError):
                    rebuild.unpack_archive(archive, root / 'out', {'matches.jsonl': '0' * 64}, {'matches.jsonl': 1})
                self.assertFalse((root / 'out').exists())

    def test_bad_digest_and_incomplete_archive_rejected(self):
        for members in [[('matches.jsonl', b'bad', 'file')], []]:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = self.make_archive(root, members)
                with self.assertRaises(ValueError):
                    rebuild.unpack_archive(archive, root / 'out', {'matches.jsonl': '0' * 64}, {'matches.jsonl': 3})

    def test_existing_destination_is_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / 'out'
            destination.mkdir()
            (destination / 'keep').write_text('keep')
            archive = self.make_archive(root, [])
            with self.assertRaises(FileExistsError):
                rebuild.unpack_archive(archive, destination, {}, {})
            self.assertEqual((destination / 'keep').read_text(), 'keep')

    def test_cluster_refuses_existing_root_before_running_postgres(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileExistsError):
                rebuild.create_cluster(Path(directory), Path('/missing/postgres'), 55439)

    def test_packaged_engine_and_frozen_manifest_authenticate(self):
        lock = rebuild.load_lock()
        rebuild.verify_engine(lock)
        self.assertEqual(lock['expected_matches'], 5215)
        self.assertEqual(lock['expected_holds'], 18)

    def test_existing_download_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary = root / '.out.download'
            temporary.write_text('keep')
            lock = {'archive_url': 'https://github.com/iam-Hemanth/cricket-extractor/releases/download/test/input.tar.gz'}
            with mock.patch.object(rebuild, 'load_lock', return_value=lock), mock.patch.object(rebuild, 'verify_engine'):
                with self.assertRaises(FileExistsError):
                    rebuild.fetch_payload(root / 'out')
            self.assertEqual(temporary.read_text(), 'keep')

    def test_user_size_ceiling_is_strict_and_preference_separate(self):
        lock = {'maximum_database_bytes': 481000000, 'preferred_database_bytes': 480000000}
        self.assertTrue(rebuild.size_flags(479999999, lock)['under_preferred_budget'])
        self.assertFalse(rebuild.size_flags(480000000, lock)['under_preferred_budget'])
        self.assertTrue(rebuild.size_flags(480999999, lock)['under_budget'])
        self.assertFalse(rebuild.size_flags(481000000, lock)['under_budget'])


if __name__ == '__main__':
    unittest.main()
