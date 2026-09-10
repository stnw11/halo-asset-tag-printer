from tools.print_test_tag import main


def test_dry_run_end_to_end_writes_valid_zpl(tmp_path, capsys):
    out_file = tmp_path / "out.zpl"
    rc = main(["--dry-run", "--dry-run-path", str(out_file)])
    assert rc == 0
    content = out_file.read_text()
    assert content.strip().startswith("^XA")
    assert content.strip().endswith("^XZ")
    assert "TAG-0123" in content
    assert "^BQN,2," in content

    captured = capsys.readouterr()
    assert "DRY_RUN" in captured.out


def test_config_error_returns_nonzero_exit(capsys):
    rc = main(["--darkness", "99"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "Configuration error" in captured.err


def test_missing_logo_asset_still_prints_logo_less_with_warning(tmp_path, caplog):
    out_file = tmp_path / "out.zpl"
    with caplog.at_level("WARNING"):
        rc = main(
            [
                "--dry-run",
                "--dry-run-path",
                str(out_file),
                "--logo-asset",
                str(tmp_path / "does-not-exist.png"),
            ]
        )
    assert rc == 0
    content = out_file.read_text()
    assert "^GFA" not in content  # no logo block emitted
    assert content.strip().startswith("^XA")
    assert "TAG-0123" in content
    assert "could not load logo" in caplog.text
