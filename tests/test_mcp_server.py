from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.config import Settings
from app.mcp_server import build_mcp_server, resource_url

pytestmark = pytest.mark.skipif(
    __import__("importlib.util", fromlist=["util"]).find_spec("mcp") is None,
    reason="optional 'mcp' package not installed",
)


def tool_names(server: object) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}  # type: ignore[attr-defined]


def test_builtin_tools_registered(fake_deps) -> None:
    server = build_mcp_server(Settings(), lambda: fake_deps)

    assert {"search", "search_docs", "fetch", "ask"} <= tool_names(server)


def test_tool_descriptions_are_overridable(fake_deps) -> None:
    settings = Settings(mcp_tool_search_description="Search the Acme corpus only.")
    server = build_mcp_server(settings, lambda: fake_deps)

    tools = {tool.name: tool.description for tool in asyncio.run(server.list_tools())}  # type: ignore[attr-defined]
    assert tools["search"] == "Search the Acme corpus only."


def test_extensions_module_can_add_tools(fake_deps, monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("grounded_rag_test_ext")

    def register(server, deps_provider, settings):
        @server.tool(description="Custom tool from an extensions module.")
        def acme_ping() -> str:
            return "pong"

    module.register = register  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "grounded_rag_test_ext", module)

    server = build_mcp_server(Settings(mcp_extensions_module="grounded_rag_test_ext"), lambda: fake_deps)

    assert "acme_ping" in tool_names(server)


def test_extensions_module_without_register_fails_loudly(fake_deps, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "grounded_rag_broken_ext", types.ModuleType("grounded_rag_broken_ext"))

    with pytest.raises(RuntimeError, match="register"):
        build_mcp_server(Settings(mcp_extensions_module="grounded_rag_broken_ext"), lambda: fake_deps)


def test_resource_url_defaults_to_localhost_with_mcp_path() -> None:
    assert resource_url(Settings(mcp_port=8090)) == "http://localhost:8090/mcp"
    assert resource_url(Settings(mcp_resource_url="https://rag.example.com")) == "https://rag.example.com/mcp"
