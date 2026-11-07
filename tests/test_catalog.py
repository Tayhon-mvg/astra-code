from __future__ import annotations

from astra import catalog


def test_free_filter_openrouter():
    assert catalog.is_free_model("openrouter", "cohere/north-mini-code:free")
    assert not catalog.is_free_model("openrouter", "openai/gpt-5")
    assert "нет суффикса" in catalog.free_status("openrouter", "openai/gpt-5")[1]


def test_free_filter_gemini_zai():
    assert catalog.is_free_model("gemini", "gemini-2.5-pro")
    assert not catalog.is_free_model("gemini", "gemini-ultra-paid")
    assert catalog.is_free_model("zai", "glm-4.7-flash")
    assert not catalog.is_free_model("zai", "glm-5-premium")


def test_split_model_ref():
    assert catalog.split_model_ref("groq:openai/gpt-oss-120b", "gemini") == (
        "groq",
        "openai/gpt-oss-120b",
    )
    assert catalog.split_model_ref("gemini-2.5-pro", "openrouter") == (
        "openrouter",
        "gemini-2.5-pro",
    )
    assert catalog.split_model_ref("openrouter:google/gemma:free", "groq") == (
        "openrouter",
        "google/gemma:free",
    )


def test_ranking_prefers_coding_model():
    coder = catalog.score_model("qwen2.5-coder-32b", "coder")
    tiny = catalog.score_model("llama3.2:3b", "coder")
    assert coder > tiny


def test_rank_candidates_respects_presets():
    ranked = catalog.rank_candidates(
        "groq",
        ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
        "coder",
    )
    assert ranked[0] == "openai/gpt-oss-120b"


def test_provider_availability(cfg):
    openrouter = catalog.PROVIDERS["openrouter"]
    assert not catalog.provider_available(openrouter, cfg)
    cfg.env["OPENROUTER_API_KEY"] = "test"
    assert catalog.provider_available(openrouter, cfg)


def test_account_id_required(cfg):
    provider = catalog.PROVIDERS["cloudflare"]
    cfg.env["CLOUDFLARE_API_TOKEN"] = "test"
    assert not catalog.provider_available(provider, cfg)
    cfg.set_option("cloudflare", "account_id", "abc")
    assert catalog.provider_available(provider, cfg)
    assert "abc" in catalog.resolve_base_url(provider, cfg)


def test_custom_models_are_two():
    assert list(catalog.COMPOSITE_MODELS) == ["astra-coder", "astra-reasoner"]
    for model in catalog.COMPOSITE_MODELS.values():
        assert model.system
        assert len(model.chain) >= 5


def test_omniroute_catalog(cfg):
    provider = catalog.PROVIDERS["omniroute"]
    assert provider.local is True
    assert provider.keyless is False
    assert catalog.is_free_model("omniroute", "kiro/deepseek-3.2")
    assert catalog.free_status("omniroute", "kiro/deepseek-3.2")[0] is True
    assert "from-omniroute" in catalog.missing_setup(provider, cfg)
    cfg.env["OMNIROUTE_API_KEY"] = "sk-test"
    assert catalog.provider_available(provider, cfg) is True
    assert "запущен" in catalog.missing_setup(provider, cfg)
