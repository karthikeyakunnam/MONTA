from backend.app.services.storage_paths import resolve_stored_mp4


def test_resolve_stored_mp4_accepts_only_files_under_the_storage_root(tmp_path):
    root = tmp_path / "storage"
    render_dir = root / "renders" / "project"
    render_dir.mkdir(parents=True)
    valid = render_dir / "final.mp4"
    valid.write_bytes(b"0" * 2048)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"0" * 2048)

    assert resolve_stored_mp4(valid, root) == valid.resolve()
    assert resolve_stored_mp4(outside, root) is None
    assert resolve_stored_mp4(render_dir / "missing.mp4", root) is None


def test_resolve_stored_mp4_rejects_symlinks_that_escape_storage(tmp_path):
    root = tmp_path / "storage"
    render_dir = root / "renders"
    render_dir.mkdir(parents=True)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"0" * 2048)
    link = render_dir / "linked.mp4"
    try:
        link.symlink_to(outside)
    except OSError as error:
        import pytest

        pytest.skip(f"symlink creation is unavailable: {error}")

    assert resolve_stored_mp4(link, root) is None
