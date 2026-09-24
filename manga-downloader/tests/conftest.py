import os
import pytest

# Match the application style; Windows native Qt controls require extra DLLs.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Fusion")


@pytest.fixture(autouse=True)
def isolated_page_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("src.utils.page_cache.registry_path", lambda: tmp_path / "page-registry.json")
