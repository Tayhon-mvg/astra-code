from __future__ import annotations


import pytest

from astra.config import Config
from astra.router import Router
from astra.sessions import Session


def test_composite_routes_to_available_provider(cfg: Config):
    cfg.env["GROQ_API_KEY"] = "gsk_test"
    cfg.data["model"] = "astra-coder"
    router = Router(cfg)
    targets = router.resolve()
    assert targets
    assert targets[0].provider.id == "groq"
    assert targets[0].model == "openai/gpt-oss-120b"
    assert targets[0].custom is not None
    assert "Astra Coder" in targets[0].custom.name


def test_composite_skips_missing_providers(cfg: Config):
    cfg.env["GEMINI_API_KEY"] = "test"
    cfg.data["model"] = "astra-reasoner"
    router = Router(cfg)
    targets = router.resolve()
    assert targets[0].provider.id == "gemini"
    assert targets[0].model == "gemini-2.5-pro"


def test_composite_requires_a_key(cfg: Config):
    cfg.data["model"] = "astra-coder"
    router = Router(cfg)
    with pytest.raises(Exception) as exc:
        router.resolve()
    assert "нет ни одного" in str(exc.value)


def test_mock_target(cfg: Config):
    router = Router(cfg)
    targets = router.resolve("mock")
    assert targets[0].provider.id == "mock"
    assert targets[0].model == "astra-demo"


def test_direct_provider_without_key(cfg: Config):
    router = Router(cfg)
    with pytest.raises(Exception) as exc:
        router.resolve("openrouter:google/gemma-4-31b-it:free")
    assert "нет ключа" in str(exc.value).lower() or "openrouter" in str(exc.value).lower()


def test_paid_model_is_blocked(cfg: Config):
    cfg.env["OPENROUTER_API_KEY"] = "test"
    router = Router(cfg)
    with pytest.raises(Exception) as exc:
        router.resolve("openrouter:openai/gpt-5")
    assert "бесплат" in str(exc.value).lower()


def test_paid_model_allowed_with_flag(cfg: Config):
    cfg.env["OPENROUTER_API_KEY"] = "test"
    cfg.data["allow_paid"] = True
    router = Router(cfg)
    target = router.resolve("openrouter:openai/gpt-5")[0]
    assert target.model == "openai/gpt-5"


def test_choose_model_respects_pin(cfg: Config):
    cfg.env["GROQ_API_KEY"] = "test"
    cfg.data["providers"] = {"groq": {"model": "qwen/qwen3.8-27b"}}
    router = Router(cfg)
    assert router.choose_model("groq", "coder") == "qwen/qwen3.8-27b"


def test_local_provider_unreachable_is_reported(cfg: Config):
    router = Router(cfg)
    state = next(s for s in router.states() if s.provider.id == "ollama")
    assert state.available is False
    assert "не запущен" in state.reason


def test_omniroute_prefers_verified_models(cfg: Config):
    cfg.env["OMNIROUTE_API_KEY"] = "sk-test"
    router = Router(cfg)
    router._local_up["omniroute"] = True
    targets = router.resolve("omniroute")
    assert targets[0].provider.id == "omniroute"
    assert targets[0].model == "kiro/qwen3-coder-next"
    assert router.choose_model("omniroute", "reasoner") == "kiro/deepseek-3.2"


def test_omniroute_requires_key(cfg: Config):
    router = Router(cfg)
    with pytest.raises(Exception) as exc:
        router.resolve("omniroute")
    assert "from-omniroute" in str(exc.value)


def test_omniroute_server_down(cfg: Config):
    cfg.env["OMNIROUTE_API_KEY"] = "sk-test"
    router = Router(cfg)
    router._local_up["omniroute"] = False
    with pytest.raises(Exception) as exc:
        router.resolve("omniroute")
    assert "не отвечает" in str(exc.value) or "не запущен" in str(exc.value)


def test_omniroute_metadata_and_ranking(cfg: Config):
    cfg.env["OMNIROUTE_API_KEY"] = "sk-test"
    router = Router(cfg)
    router._local_up["omniroute"] = True
    router._cache["omniroute"] = {
        "ts": 1,
        "models": [
            {
                "id": "cfp/dead",
                "ctx": 128000,
                "free": True,
                "owned_by": "cloudflare-playground",
                "type": "text",
                "capabilities": {"tool_calling": True},
            },
            {
                "id": "kiro/extra",
                "ctx": 200000,
                "free": True,
                "owned_by": "kiro",
                "type": "text",
                "capabilities": {"tool_calling": True},
            },
        ],
    }
    models = router.all_models("omniroute")
    by_id = {m["id"]: m for m in models}
    assert by_id["kiro/extra"]["owner"] == "kiro"
    assert by_id["kiro/extra"]["tool_calling"] is True
    ranked = router.ranked_models("omniroute", "coder")
    assert [m["id"] for m in ranked[:5]] == [
        "kiro/qwen3-coder-next",
        "kiro/deepseek-3.2",
        "kiro/glm-5",
        "kiro/minimax-m2.5",
        "kiro/minimax-m2.1",
    ]


def test_session_persistence(cfg: Config):
    session = Session.new(cfg.cwd, "groq", "astra-coder")
    session.add({"role": "user", "content": "hello"})
    session.add({"role": "assistant", "content": "hi"})
    session.usage.add(100, 20, 1, 1.5)
    path = session.save(cfg.home)
    assert path.exists()

    from astra.sessions import find_session, latest_session, list_sessions

    loaded = list_sessions(cfg.home)[0]
    assert loaded.id == session.id
    assert loaded.messages[1]["content"] == "hi"
    assert loaded.usage.total == 120
    assert latest_session(cfg.home, cfg.cwd).id == session.id
    assert find_session(cfg.home, session.id[:8]).id == session.id


def test_session_reset(cfg: Config):
    session = Session.new(cfg.cwd)
    session.add({"role": "user", "content": "hello"})
    session.reset()
    assert session.messages == []
    assert session.usage.total == 0
