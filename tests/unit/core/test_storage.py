import os
import sys

import pytest

from bankwise.core.errors import ConfigError, InvalidInputError
from bankwise.core.storage import SecureFileStore, StorageSettings, detect_image_type

JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body" * 4
PNG = b"\x89PNG\r\n\x1a\n" + b"fake-png-body" * 4
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    settings = StorageSettings(base_dir="vault", ttl_seconds=100, max_bytes=1000)
    return SecureFileStore(settings, root=tmp_path, clock=clock)


def age(store, file_id, clock, seconds_old):
    path = store._path(file_id)
    os.utime(path, (clock.now - seconds_old, clock.now - seconds_old))


def test_image_signatures():
    assert detect_image_type(JPEG) == "jpeg" and detect_image_type(PNG) == "png"
    for other in (b"", b"GIF89a", b"%PDF-1.4", b"plain text", b"<svg></svg>", b"MZ\x90\x00"):
        assert detect_image_type(other) is None


def test_round_trip_with_opaque_random_ids(store):
    first, second = store.save(JPEG), store.save(PNG)
    assert len(first) == 32 and first != second and all(c in "0123456789abcdef" for c in first)
    assert store.load(first) == JPEG and store.load(second) == PNG


@pytest.mark.parametrize("data", [b"", b"not an image", b"GIF89a....", bytearray(), None, "text", 5])
def test_only_nonempty_jpeg_or_png_images_are_accepted(store, data):
    with pytest.raises(InvalidInputError):
        store.save(data)


def test_size_limit_and_generic_blobs(store):
    with pytest.raises(InvalidInputError):
        store.save(JPEG + b"x" * 1000)
    assert store.load(store.save(b'{"k": 1}', require_image=False)) == b'{"k": 1}'


@pytest.mark.parametrize("bad", ["../x", "..", "", "g" * 32, "A" * 32, "0" * 31, "0" * 33, "0" * 32 + ".bin",
                                 "0123456789abcdef0123456789abcde/", "../" + "0" * 29, None, 5])
def test_ids_that_are_not_ours_are_rejected_before_touching_the_disk(store, bad):
    for call in (store.load, store.delete):
        with pytest.raises(InvalidInputError):
            call(bad)


def test_unknown_id_is_a_generic_not_found(store):
    with pytest.raises(InvalidInputError) as info:
        store.load("0" * 32)
    assert "not found" in info.value.detail and "0" * 32 not in repr(info.value.to_public())


def test_expired_files_are_unreadable_and_removed(store, clock):
    file_id = store.save(JPEG)
    age(store, file_id, clock, 99)
    assert store.load(file_id) == JPEG
    age(store, file_id, clock, 100)
    with pytest.raises(InvalidInputError):
        store.load(file_id)
    assert not store._path(file_id).exists()


def test_purge_removes_only_expired_files_it_created(store, clock):
    old, fresh = store.save(JPEG), store.save(PNG)
    age(store, old, clock, 500)
    foreign = store._dir / "notes.txt"
    foreign.write_text("someone else's file")
    os.utime(foreign, (clock.now - 9999, clock.now - 9999))
    assert store.purge_expired() == 1
    assert not store._path(old).exists() and store._path(fresh).exists() and foreign.exists()
    assert store.purge_expired() == 0


def test_delete_overwrites_then_removes(store):
    file_id = store.save(JPEG)
    path = store._path(file_id)
    assert store.delete(file_id) is True and not path.exists()
    assert store.delete(file_id) is False


def test_overwriting_an_existing_file_is_impossible(store, monkeypatch):
    monkeypatch.setattr("bankwise.core.storage.uuid.uuid4", lambda: type("U", (), {"hex": "f" * 32})())
    store.save(JPEG)
    with pytest.raises(FileExistsError):
        store.save(PNG)
    assert store.load("f" * 32) == JPEG


@POSIX
def test_files_and_directory_are_owner_only(store):
    file_id = store.save(JPEG)
    assert (store._path(file_id).stat().st_mode & 0o777) == 0o600
    assert (store._dir.stat().st_mode & 0o777) == 0o700


@POSIX
def test_a_symlinked_storage_directory_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "link")
    with pytest.raises(ConfigError):
        SecureFileStore(StorageSettings(base_dir="link"), root=tmp_path)


def test_relative_base_dirs_resolve_against_the_given_root(tmp_path):
    store = SecureFileStore(StorageSettings(base_dir="a/b"), root=tmp_path)
    assert store._dir == (tmp_path / "a" / "b").resolve() and store._dir.is_dir()


@pytest.mark.parametrize("kwargs", [{"base_dir": ""}, {"base_dir": "  "}, {"base_dir": 5}, {"ttl_seconds": 0},
                                    {"ttl_seconds": True}, {"ttl_seconds": 8 * 86400}, {"max_bytes": 0},
                                    {"max_bytes": 10 ** 9}, {"max_bytes": "10"}])
def test_invalid_storage_settings_are_rejected(kwargs):
    with pytest.raises(ConfigError):
        StorageSettings(**kwargs)
