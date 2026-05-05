from app.main import _safe_upload_name


def test_safe_upload_name_strips_path_traversal():
    name = _safe_upload_name("../../evil.xlsx")

    assert ".." not in name
    assert "/" not in name
    assert name.endswith("evil.xlsx")


def test_safe_upload_name_replaces_unsafe_characters():
    name = _safe_upload_name("store import (final).xlsx")

    assert " " not in name
    assert "(" not in name
    assert ")" not in name
    assert name.endswith("store_import__final_.xlsx")
