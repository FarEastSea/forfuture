from app.browser.fingerprint import fingerprint_for_key, validate_fingerprint


def test_fingerprint_is_internally_consistent():
    fp = fingerprint_for_key("xhs:1")
    assert validate_fingerprint(fp) == []
    assert fingerprint_for_key("xhs:1") == fp
