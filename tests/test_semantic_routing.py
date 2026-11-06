import pytest
from unittest.mock import patch
from agent.tools import route_tools, CORE_TOOL_NAMES, TOOL_DEFINITIONS

def test_route_tools_always_includes_core_tools():
    """Phase 7c Item 6: Core tools are always present by default."""
    tools = route_tools("")
    tool_names = {t["function"]["name"] for t in tools}
    for core in CORE_TOOL_NAMES:
        assert core in tool_names, f"Core tool '{core}' must be included by default"


def test_semantic_routing_activates_github_domain():
    """Phase 7c Item 6: Domain tools are attached semantically via embedding similarity."""
    query = "Please commit my changes to the repository and update the readme file"
    tools = route_tools(query)
    tool_names = {t["function"]["name"] for t in tools}
    
    assert "list_github_repos" in tool_names or "update_github_file" in tool_names, (
        "GitHub domain tools must be activated for git/repo intent"
    )


def test_semantic_routing_activates_notion_domain():
    """Phase 7c Item 6: Domain tools are attached semantically for workspace/notion intent."""
    query = "Create a documentation page in my workspace with these meeting notes"
    tools = route_tools(query)
    tool_names = {t["function"]["name"] for t in tools}
    
    assert "create_notion_page" in tool_names or "list_notion_pages" in tool_names, (
        "Notion domain tools must be activated for workspace documentation intent"
    )


def test_dead_keyword_dict_removed():
    """Phase 7c Item 6: Verify is_action_or_tool_intent was deleted and does not exist in agent.tools."""
    import agent.tools as tools_mod
    assert not hasattr(tools_mod, "is_action_or_tool_intent"), (
        "Dead keyword router 'is_action_or_tool_intent' must be excised"
    )
