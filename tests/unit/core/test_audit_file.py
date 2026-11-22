import json
import os
import sys

import pytest

from bankwise.core.audit import (
    AuditEvent, AuditOutcome, AuditSettings, GENESIS, HashChainedFileAuditSink, verify_audit_file,
)
from bankwise.core.errors import BankWiseError, ConfigError
from bankwise.core.pii import Sensitive

KEY = Sensitive("audit-key-Zq8Xv3Nc7Rt1Ws5Ye9Bu2Hd6Jf4Lg0")
OTHER_KEY = Sensitive("different-key-Aa1Bb2Cc3Dd4Ee5Ff6Gg7Hh8Ii9")
POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits only")


def event(n=0, **kwargs):
    return AuditEvent("identity.submit", AuditOutcome.SUCCESS, actor_id="user-1", metadata={"n": n}, **kwargs)


def filled(tmp_path, count=5):
    path = tmp_path / "audit" / "audit.jsonl"
    sink = HashChainedFileAuditSink(path, KEY)
    for i in range(count):
        sink.write(event(i))
    return path, sink


def lines(path):
    return path.read_bytes().splitlines()


def test_records_are_chained_verifiable_and_contain_no_secrets(tmp_path):
    path, sink = filled(tmp_path)
    result = sink.verify()
    assert (result.ok, result.records) == (True, 5) and result.last_mac == json.loads(lines(path)[-1])["mac"]
    first = json.loads(lines(path)[0])
    assert first["seq"] == 1 and first["prev"] == GENESIS and first["event"]["event_type"] == "identity.submit"
    assert "audit-key" not in path.read_text()


def test_editing_a_record_is_detected(tmp_path):
    path, _ = filled(tmp_path)
    data = path.read_bytes().replace(b'"actor_id":"user-1"', b'"actor_id":"user-9"', 1)
    path.write_bytes(data)
    result = verify_audit_file(path, KEY)
    assert not result.ok and "bad MAC at record 1" in result.error


def test_deleting_or_reordering_records_is_detected(tmp_path):
    path, _ = filled(tmp_path)
    original = lines(path)
    path.write_bytes(b"\n".join(original[:2] + original[3:]) + b"\n")
    deleted = verify_audit_file(path, KEY)
    assert not deleted.ok and "chain broken at record 3" in deleted.error
    path.write_bytes(b"\n".join([original[1], original[0]] + original[2:]) + b"\n")
    reordered = verify_audit_file(path, KEY)
    assert not reordered.ok and "chain broken at record 1" in reordered.error


def test_forged_records_without_the_key_are_detected(tmp_path):
    path, _ = filled(tmp_path, 2)
    forged = {"seq": 3, "event": event(99).to_dict(), "prev": json.loads(lines(path)[-1])["mac"], "mac": "0" * 64}
    with open(path, "ab") as handle:
        handle.write((json.dumps(forged) + "\n").encode())
    assert not verify_audit_file(path, KEY).ok
    assert not verify_audit_file(filled(tmp_path / "b", 2)[0], OTHER_KEY).ok       # wrong key


def test_truncating_the_tail_is_NOT_detected_which_is_why_the_last_mac_must_be_anchored_elsewhere(tmp_path):
    path, sink = filled(tmp_path, 5)
    anchored = sink.verify()
    path.write_bytes(b"\n".join(lines(path)[:3]) + b"\n")
    result = verify_audit_file(path, KEY)
    assert result.ok and result.records == 3                                          # the documented limit
    assert (result.records, result.last_mac) != (anchored.records, anchored.last_mac)  # an external anchor would catch it


def test_garbage_and_partial_lines_fail_verification(tmp_path):
    path, _ = filled(tmp_path, 2)
    with open(path, "ab") as handle:
        handle.write(b"not json\n")
    assert not verify_audit_file(path, KEY).ok
    path2, _ = filled(tmp_path / "c", 2)
    with open(path2, "ab") as handle:
        handle.write(b'{"seq":3')
    assert "incomplete" in verify_audit_file(path2, KEY).error


def test_restarting_continues_the_chain_and_refuses_a_tampered_file(tmp_path):
    path, _ = filled(tmp_path, 3)
    sink = HashChainedFileAuditSink(path, KEY)
    sink.write(event(3))
    assert verify_audit_file(path, KEY).records == 4
    path.write_bytes(path.read_bytes().replace(b'"n":1', b'"n":7', 1))
    with pytest.raises(BankWiseError) as info:
        HashChainedFileAuditSink(path, KEY)
    assert "failed verification" in info.value.detail


def test_a_full_file_fails_loudly_instead_of_dropping_or_rotating(tmp_path):
    sink = HashChainedFileAuditSink(tmp_path / "a.jsonl", KEY, max_bytes=1500)
    written = 0
    with pytest.raises(BankWiseError) as info:
        for i in range(100):
            sink.write(event(i))
            written += 1
    assert "full" in info.value.detail and written >= 1 and verify_audit_file(tmp_path / "a.jsonl", KEY).records == written


def test_each_write_is_one_complete_line_and_other_threads_cannot_interleave(tmp_path):
    import threading
    path = tmp_path / "t.jsonl"
    sink = HashChainedFileAuditSink(path, KEY)
    workers = [threading.Thread(target=lambda: [sink.write(event(i)) for i in range(25)]) for _ in range(6)]
    [w.start() for w in workers]
    [w.join() for w in workers]
    assert verify_audit_file(path, KEY).records == 150


@POSIX
def test_the_audit_file_is_owner_only_and_never_follows_a_symlink(tmp_path):
    path, _ = filled(tmp_path, 1)
    assert (path.stat().st_mode & 0o777) == 0o600
    victim = tmp_path / "victim"
    victim.write_text("precious")
    link = tmp_path / "linked.jsonl"
    os.symlink(victim, link)
    with pytest.raises(ConfigError):
        HashChainedFileAuditSink(link, KEY)
    assert victim.read_text() == "precious"


@pytest.mark.parametrize("key", ["x" * 40, Sensitive("short"), Sensitive(b"x" * 40), None])
def test_the_hmac_key_must_be_a_long_sensitive_string(tmp_path, key):
    with pytest.raises(ConfigError):
        HashChainedFileAuditSink(tmp_path / "k.jsonl", key)


@pytest.mark.parametrize("kwargs", [{"base_dir": ""}, {"filename": "../x"}, {"filename": ".hidden"}, {"filename": "a b"},
                                    {"max_bytes": 10}, {"max_bytes": True}, {"max_bytes": 2 * 1024 ** 3}])
def test_audit_settings_are_validated(kwargs):
    with pytest.raises(ConfigError):
        AuditSettings(**kwargs)


def test_every_record_is_flushed_to_disk_before_write_returns(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), real(fd))[1])
    sink = HashChainedFileAuditSink(tmp_path / "f.jsonl", KEY)
    for i in range(3):
        sink.write(event(i))
    assert len(calls) == 3
