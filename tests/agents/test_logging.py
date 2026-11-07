"""What the agent writes to the log: no shopper text at INFO."""

from tests.agents.test_single_use_approvals import _assistant, act


def test_user_text_is_not_logged_at_info(chat_model_replying, cocktail_catalog, caplog):
    """The audit found `agent step tool/SearchCocktails: Found 3 drinks for "save mojito to
    my shortlist".` in the INFO log."""
    secret = "mojito for my pregnant sister at 1 Main St"
    assistant = _assistant(
        chat_model_replying,
        cocktail_catalog,
        act("SearchCocktails", {"query": secret}),
        "Here are some drinks.",
    )
    with caplog.at_level("INFO", logger="curio"):
        assistant.reply(secret, [])
    info = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert not any("pregnant" in m or "Main St" in m for m in info)
    assert any(m.startswith("agent step tool/SearchCocktails (") for m in info)
    caplog.clear()
    with caplog.at_level("DEBUG", logger="curio"):
        _assistant(
            chat_model_replying,
            cocktail_catalog,
            act("SearchCocktails", {"query": secret}),
            "Here are some drinks.",
        ).reply(secret, [])
    debug = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    assert any("pregnant" in m for m in debug)  # still there when you ask for it
