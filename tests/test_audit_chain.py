import threading
from guardian.storage import GuardianStorage


def test_audit_chain_concurrent_writes_and_repeated_verification(tmp_path):
    db_path = tmp_path / "concurrent_audit.db"
    storage = GuardianStorage(db_path=db_path)

    def worker(idx: int):
        for j in range(5):
            storage.record_audit(
                f"2026-11-28T00:0{idx}:{j:02d}Z",
                "new_checkout",
                "STAGE_1",
                "STAGE_2",
                "ADVANCE",
                f"worker-{idx}-{j}",
                {"i": idx, "j": j},
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    res1 = storage.verify_audit_chain()
    res2 = storage.verify_audit_chain()
    assert res1 == res2
    assert res1["valid"] is True
    assert res1["checked_entries"] == 20
    storage.close()


def test_audit_chain_restart_recovery(tmp_path):
    db_path = tmp_path / "restart_audit.db"
    s1 = GuardianStorage(db_path=db_path)
    s1.record_audit("2026-11-28T00:00:00Z", "flag_a", "PENDING", "STAGE_1", "ADVANCE", "step 1", {"pct": 1})
    s1.record_audit("2026-11-28T00:01:00Z", "flag_a", "STAGE_1", "STAGE_2", "ADVANCE", "step 2", {"pct": 5})
    s1.close()

    s2 = GuardianStorage(db_path=db_path)
    s2.record_audit("2026-11-28T00:02:00Z", "flag_a", "STAGE_2", "ROLLED_BACK", "ROLLBACK", "step 3", {"pct": 0})
    res = s2.verify_audit_chain()
    assert res["valid"] is True
    assert res["checked_entries"] == 3
    s2.close()


def test_audit_chain_detects_modification_deletion_reordering_and_bad_head(tmp_path):
    # 1. Modification
    db1 = tmp_path / "mod.db"
    s = GuardianStorage(db_path=db1)
    s.record_audit("2026-11-28T00:00:00Z", "f", "PENDING", "STAGE_1", "ADVANCE", "r1", {})
    s.record_audit("2026-11-28T00:01:00Z", "f", "STAGE_1", "STAGE_2", "ADVANCE", "r2", {})
    with s._lock, s._conn:
        s._conn.execute("UPDATE audit_log SET reason = 'evil' WHERE id = 1")
    assert s.verify_audit_chain()["valid"] is False
    s.close()

    # 2. Middle record deletion & tail record deletion
    db2 = tmp_path / "del.db"
    s = GuardianStorage(db_path=db2)
    s.record_audit("2026-11-28T00:00:00Z", "f", "PENDING", "STAGE_1", "ADVANCE", "r1", {})
    s.record_audit("2026-11-28T00:01:00Z", "f", "STAGE_1", "STAGE_2", "ADVANCE", "r2", {})
    s.record_audit("2026-11-28T00:02:00Z", "f", "STAGE_2", "ROLLED_BACK", "ROLLBACK", "r3", {})
    with s._lock, s._conn:
        s._conn.execute("DELETE FROM audit_log WHERE id = 2")
    assert s.verify_audit_chain()["valid"] is False
    s.close()

    db2b = tmp_path / "del_tail.db"
    s = GuardianStorage(db_path=db2b)
    s.record_audit("2026-11-28T00:00:00Z", "f", "PENDING", "STAGE_1", "ADVANCE", "r1", {})
    s.record_audit("2026-11-28T00:01:00Z", "f", "STAGE_1", "STAGE_2", "ADVANCE", "r2", {})
    with s._lock, s._conn:
        s._conn.execute("DELETE FROM audit_log WHERE id = 2")
    assert s.verify_audit_chain()["valid"] is False
    s.close()

    # 3. Record reordering (swap entry_hash/prev_hash order by swapping content of id=1 and id=2)
    db3 = tmp_path / "reorder.db"
    s = GuardianStorage(db_path=db3)
    s.record_audit("2026-11-28T00:00:00Z", "f", "PENDING", "STAGE_1", "ADVANCE", "r1", {})
    s.record_audit("2026-11-28T00:01:00Z", "f", "STAGE_1", "STAGE_2", "ADVANCE", "r2", {})
    with s._lock, s._conn:
        r1 = dict(s._conn.execute("SELECT * FROM audit_log WHERE id = 1").fetchone())
        r2 = dict(s._conn.execute("SELECT * FROM audit_log WHERE id = 2").fetchone())
        s._conn.execute("UPDATE audit_log SET reason = ?, prev_hash = ?, entry_hash = ? WHERE id = 1", (r2["reason"], r2["prev_hash"], r2["entry_hash"]))
        s._conn.execute("UPDATE audit_log SET reason = ?, prev_hash = ?, entry_hash = ? WHERE id = 2", (r1["reason"], r1["prev_hash"], r1["entry_hash"]))
    assert s.verify_audit_chain()["valid"] is False
    s.close()

    # 4. Invalid chain head
    db4 = tmp_path / "bad_head.db"
    s = GuardianStorage(db_path=db4)
    s.record_audit("2026-11-28T00:00:00Z", "f", "PENDING", "STAGE_1", "ADVANCE", "r1", {})
    with s._lock, s._conn:
        s._conn.execute("UPDATE audit_meta SET value = 'deadbeef' WHERE key = 'chain_head'")
    assert s.verify_audit_chain()["valid"] is False
    s.close()
