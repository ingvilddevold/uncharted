from pathlib import Path

from uncharted.config import load_config


def test_defaults_when_no_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.area.crs == "EPSG:25832"
    assert cfg.matching.buffer_m == 20


def test_local_overrides_merge(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text(
        '[area]\nplace = "Testby"\n[matching]\nbuffer_m = 20\nedge_threshold = 0.9\n'
    )
    (tmp_path / "config.local.toml").write_text("[matching]\nbuffer_m = 15\n")
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.area.place == "Testby"
    assert cfg.matching.buffer_m == 15
    assert cfg.matching.edge_threshold == 0.9  # untouched key survives the merge
