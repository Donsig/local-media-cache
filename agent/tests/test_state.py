"""Tests for state.py — uses real SQLite via tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from syncarr_agent.state import StateDB


def test_upsert_and_get(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    db.upsert(1, "gid1", Path("/lib/1/file.mkv"), "active")
    rec = db.get(1)
    assert rec is not None
    assert rec.asset_id == 1
    assert rec.gid == "gid1"
    assert rec.local_path == Path("/lib/1/file.mkv")
    assert rec.status == "active"
    assert rec.started_at != ""


def test_set_failed(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    db.upsert(2, "gid2", Path("/lib/2/file.mkv"), "active")
    db.set_failed(2)
    rec = db.get(2)
    assert rec is not None
    assert rec.status == "failed"


def test_delete(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    db.upsert(3, "gid3", Path("/lib/3/file.mkv"), "active")
    db.delete(3)
    assert db.get(3) is None


def test_all(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    db.upsert(10, "gidA", Path("/lib/10/a.mkv"), "active")
    db.upsert(11, "gidB", Path("/lib/11/b.mkv"), "active")
    records = db.all()
    assert len(records) == 2
    asset_ids = {r.asset_id for r in records}
    assert asset_ids == {10, 11}


def test_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    db = StateDB(db_path)
    db.upsert(99, "gidP", Path("/lib/99/p.mkv"), "active")
    del db  # close connection
    db2 = StateDB(db_path)
    rec = db2.get(99)
    assert rec is not None
    assert rec.gid == "gidP"


def test_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "state.db"
    db = StateDB(nested)
    db.upsert(1, "gid1", Path("/lib/1/f.mkv"), "active")
    assert nested.exists()


def test_upsert_overwrites(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    db.upsert(5, "gidOld", Path("/lib/5/file.mkv"), "active")
    db.upsert(5, "gidNew", Path("/lib/5/file.mkv"), "active")
    rec = db.get(5)
    assert rec is not None
    assert rec.gid == "gidNew"
