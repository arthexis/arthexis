import importlib


def test_settings_do_not_expose_mcp_configuration():
    settings = importlib.import_module("config.settings")

    mcp_exports = [name for name in dir(settings) if name.startswith("MCP_")]
    assert mcp_exports == []
    assert not hasattr(settings, "_env_int")
