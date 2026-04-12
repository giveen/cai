import pytest

from cai.memory.paging import VirtualContextManager


def test_vcm_force_page_too_large_and_force_behaviour():
    v = VirtualContextManager(max_active_tokens=2)
    # Create a page that's larger than the budget (tokens ~ len//4)
    large_text = "x" * 20
    v.add_page("big", large_text)

    res = v.page_in("big", force=False)
    assert res.get("status") == "error" and res.get("reason") == "page_too_large"

    # Force should succeed even if it exceeds budget
    res_force = v.page_in("big", force=True)
    assert res_force.get("status") == "paged_in"
    pg = v.get_page("big")
    assert pg is not None and pg.in_gpu is True


def test_vcm_export_import_roundtrip():
    v = VirtualContextManager(max_active_tokens=1024)
    v.add_page("p1", "hello" * 200)
    v.page_in("p1", force=True)

    state = v.export_state()
    v2 = VirtualContextManager()
    v2.import_state(state)

    names = [p["name"] for p in v2.list_pages()]
    assert "p1" in names
