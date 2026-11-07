"""Provider and model catalogue.

Only providers with a real free tier are shipped.  Model ids come from the
public free-tier dataset snapshot (2026-10) and are refreshed at runtime from
each provider's ``/models`` endpoint.  Free-only enforcement lives in
:func:`free_status`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .config import Config


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    ctx: int = 128_000
    rank: int = 0
    note: str = ""


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    env_key: str | None
    signup_url: str
    docs_url: str
    free_note: str
    kind: str = "openai"            # openai | local | cloudflare | mock
    models: tuple[ModelInfo, ...] = ()
    dynamic: bool = True
    requires_account_id: bool = False
    headers: tuple[tuple[str, str], ...] = ()
    note: str = ""
    local: bool = False
    keyless: bool = False

    @property
    def short(self) -> str:
        return self.name.split(" (")[0]


OPENROUTER_MODELS = (
    ModelInfo("cohere/north-mini-code:free", "North Mini Code", 128_000, 1, "кодовая модель Cohere"),
    ModelInfo("poolside/laguna-s-2.1:free", "Laguna S 2.1", 128_000, 2, "агентная кодовая модель"),
    ModelInfo("google/gemma-4-31b-it:free", "Gemma 4 31B", 128_000, 3, "сильный универсал"),
    ModelInfo("google/gemma-4-26b-a4b-it:free", "Gemma 4 26B A4B", 128_000, 4, "быстрый MoE"),
    ModelInfo("nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "Nemotron 3 Nano", 128_000, 5, "reasoning"),
    ModelInfo("liquid/lfm-2.5-2.6b:free", "LFM 2.5 2.6B", 32_000, 6, "крошечная, очень быстрая"),
)

GROQ_MODELS = (
    ModelInfo("openai/gpt-oss-120b", "GPT-OSS 120B", 131_072, 1, "флагман free-tier"),
    ModelInfo("qwen/qwen3.8-27b", "Qwen 3.8 27B", 131_072, 2, "сильный код и рассуждения"),
    ModelInfo("openai/gpt-oss-20b", "GPT-OSS 20B", 131_072, 3, "быстрый запасной"),
)

GEMINI_MODELS = (
    ModelInfo("gemini-2.5-pro", "Gemini 2.5 Pro", 1_048_576, 1, "frontier-класс бесплатно"),
    ModelInfo("gemini-2.5-flash", "Gemini 2.5 Flash", 1_048_576, 2, "быстрый и большой контекст"),
    ModelInfo("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 1_048_576, 3, "самый щедрый rate-limit"),
)

SAMBANOVA_MODELS = (
    ModelInfo("DeepSeek-V3.1", "DeepSeek V3.1", 131_072, 1, "сильный код"),
    ModelInfo("gpt-oss-120b", "GPT-OSS 120B", 131_072, 2, "быстрый флагман"),
    ModelInfo("Meta-Llama-3.3-70B-Instruct", "Llama 3.3 70B", 131_072, 3, "универсал"),
)

ZAI_MODELS = (
    ModelInfo("glm-4.7-flash", "GLM-4.7 Flash", 131_072, 1, "$0 навсегда"),
    ModelInfo("glm-4.5-flash", "GLM-4.5 Flash", 131_072, 2, "$0 навсегда"),
    ModelInfo("glm-4.6v-flash", "GLM-4.6V Flash", 131_072, 3, "vision, $0"),
)

CLOUDFLARE_MODELS = (
    ModelInfo("@cf/qwen/qwen2.5-coder-32b-instruct", "Qwen2.5 Coder 32B", 32_768, 1, "код"),
    ModelInfo("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "Llama 3.3 70B", 32_768, 2, "универсал"),
    ModelInfo("@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", "DeepSeek R1 Distill 32B", 32_768, 3, "reasoning"),
    ModelInfo("@cf/meta/llama-3.1-8b-instruct", "Llama 3.1 8B", 32_768, 4, "быстрый"),
)

# Verified live against the local OmniRoute 3.8.52 on 2026-11-07. The remaining
# ~530 catalog models are fetched dynamically but are not claimed as working:
# most fail because an OAuth/CLI/provider runtime is missing or rate-limited.
OMNIROUTE_MODELS = (
    ModelInfo("kiro/qwen3-coder-next", "Qwen3 Coder Next · Kiro", 200_000, 1, "проверено 2026-11-07"),
    ModelInfo("kiro/deepseek-3.2", "DeepSeek V3.2 · Kiro", 200_000, 2, "проверено 2026-11-07"),
    ModelInfo("kiro/glm-5", "GLM-5 · Kiro", 200_000, 3, "проверено 2026-11-07"),
    ModelInfo("kiro/minimax-m2.5", "MiniMax M2.5 · Kiro", 200_000, 4, "проверено 2026-11-07"),
    ModelInfo("kiro/minimax-m2.1", "MiniMax M2.1 · Kiro", 200_000, 5, "проверено 2026-11-07"),
    ModelInfo("auto/best-free", "OmniRoute Auto Free", 400_000, 20, "агрегатор; сейчас нестабилен"),
    ModelInfo("auto/coding:free", "OmniRoute Coding Free", 400_000, 21, "агрегатор; сейчас нестабилен"),
)

PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        env_key="OPENROUTER_API_KEY",
        signup_url="https://openrouter.ai/keys",
        docs_url="https://openrouter.ai/docs/api-reference/limits",
        free_note="модели с суффиксом :free · 20 req/min · 50 req/день (1000 после разовой покупки 10 кредитов)",
        models=OPENROUTER_MODELS,
        headers=(("X-Title", "Astra Code"),),
        note="Самый широкий выбор бесплатных open-weight моделей. Astra блокирует платные модели без :free.",
    ),
    "gemini": Provider(
        id="gemini",
        name="Google Gemini (AI Studio)",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        env_key="GEMINI_API_KEY",
        signup_url="https://aistudio.google.com/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs/rate-limits",
        free_note="Gemini 2.5 Pro / Flash / Flash-Lite бесплатно, лимиты видны в AI Studio",
        models=GEMINI_MODELS,
        note="Единственный frontier-класс с настоящим бесплатным tier. Промпты free-tier могут использоваться Google для улучшения продуктов.",
    ),
    "groq": Provider(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        env_key="GROQ_API_KEY",
        signup_url="https://console.groq.com/keys",
        docs_url="https://console.groq.com/docs/rate-limits",
        free_note="GPT-OSS 120B и Qwen: ~30 req/min · 1000 req/день · 200k токенов/день",
        models=GROQ_MODELS,
        note="Самая низкая задержка. Нужна верификация телефона при регистрации.",
    ),
    "sambanova": Provider(
        id="sambanova",
        name="SambaNova Cloud",
        base_url="https://api.sambanova.ai/v1",
        env_key="SAMBANOVA_API_KEY",
        signup_url="https://cloud.sambanova.ai/keys",
        docs_url="https://cloud.sambanova.ai/plans",
        free_note="free tier без карты: ~20 req/min · 20 req/день · 200k токенов/день на модель",
        models=SAMBANOVA_MODELS,
        note="Free tier действует, пока к аккаунту не привязана карта.",
    ),
    "zai": Provider(
        id="zai",
        name="Z.ai (GLM)",
        base_url="https://api.z.ai/api/paas/v4",
        env_key="ZAI_API_KEY",
        signup_url="https://z.ai/manage-apikey/apikey-list",
        docs_url="https://docs.z.ai/guides/overview/pricing",
        free_note="GLM-4.7-Flash / 4.5-Flash / 4.6V-Flash — $0 бессрочно",
        models=ZAI_MODELS,
        note="Flash-модели официально стоят $0. Условия запрещают обучать конкурентные модели.",
    ),
    "cloudflare": Provider(
        id="cloudflare",
        name="Cloudflare Workers AI",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        env_key="CLOUDFLARE_API_TOKEN",
        signup_url="https://dash.cloudflare.com/profile/api-tokens",
        docs_url="https://developers.cloudflare.com/workers-ai/platform/pricing/",
        free_note="10 000 Neurons/день · сброс в 00:00 UTC",
        models=CLOUDFLARE_MODELS,
        kind="cloudflare",
        requires_account_id=True,
        note="Нужны API token и Account ID. Часть новых моделей требует платный Workers.",
    ),
    "mistral": Provider(
        id="mistral",
        name="Mistral La Plateforme",
        base_url="https://api.mistral.ai/v1",
        env_key="MISTRAL_API_KEY",
        signup_url="https://console.mistral.ai/api-keys",
        docs_url="https://docs.mistral.ai/admin/billing-usage/usage-limits",
        free_note="Free mode без карты, лимиты в консоли (Admin → API → Limits)",
        models=(),
        note="В free mode Mistral может использовать данные для обучения — отключается в админке.",
    ),
    "ollama-cloud": Provider(
        id="ollama-cloud",
        name="Ollama Cloud",
        base_url="https://ollama.com/v1",
        env_key="OLLAMA_API_KEY",
        signup_url="https://ollama.com/settings/keys",
        docs_url="https://docs.ollama.com/cloud",
        free_note="стартовые кредиты на free-плане, 1 параллельный запрос",
        models=(),
        note="Облачные open-weight модели (qwen3-coder, gpt-oss, deepseek) по кредитам.",
    ),
    "omniroute": Provider(
        id="omniroute",
        name="OmniRoute (локальный)",
        base_url="http://127.0.0.1:20128/v1",
        env_key="OMNIROUTE_API_KEY",
        signup_url="https://omniroute.online",
        docs_url="https://github.com/diegosouzapw/OmniRoute",
        free_note="537 моделей / 11 free-провайдеров; проверены 5 моделей Kiro",
        models=OMNIROUTE_MODELS,
        kind="local",
        local=True,
        keyless=False,
        note=(
            "Локальный роутер. Проверено 2026-11-07: kiro/qwen3-coder-next, "
            "kiro/deepseek-3.2, kiro/glm-5, kiro/minimax-m2.5, kiro/minimax-m2.1. "
            "auto/best-free сейчас падает на Cloudflare Playground."
        ),
    ),
    "ollama": Provider(
        id="ollama",
        name="Ollama (локально)",
        base_url="http://127.0.0.1:11434/v1",
        env_key=None,
        signup_url="https://ollama.com/download",
        docs_url="https://docs.ollama.com/openai",
        free_note="полностью бесплатно и приватно · работает без интернета",
        models=(
            ModelInfo("qwen2.5-coder:14b", "Qwen2.5 Coder 14B", 32_768, 1, "рекомендуется"),
            ModelInfo("qwen2.5-coder:7b", "Qwen2.5 Coder 7B", 32_768, 2, "для 8 ГБ RAM"),
            ModelInfo("deepseek-r1:8b", "DeepSeek R1 8B", 32_768, 3, "reasoning"),
            ModelInfo("llama3.2:3b", "Llama 3.2 3B", 131_072, 4, "совсем лёгкая"),
        ),
        kind="local",
        local=True,
        keyless=True,
        note="Установите Ollama и выполните: ollama pull qwen2.5-coder:14b",
    ),
    "lmstudio": Provider(
        id="lmstudio",
        name="LM Studio (локально)",
        base_url="http://127.0.0.1:1234/v1",
        env_key=None,
        signup_url="https://lmstudio.ai/",
        docs_url="https://lmstudio.ai/docs/app/api",
        free_note="локальный OpenAI-совместимый сервер · бесплатно",
        models=(),
        kind="local",
        local=True,
        keyless=True,
        note="Включите Local Server в LM Studio (Developer → Start Server).",
    ),
    "custom": Provider(
        id="custom",
        name="Свой OpenAI-совместимый endpoint",
        base_url="",
        env_key="CUSTOM_API_KEY",
        signup_url="",
        docs_url="",
        free_note="любой локальный/бесплатный OpenAI-compatible API",
        models=(),
        kind="local",
        local=True,
        keyless=True,
        note="Задайте base_url: astra auth set custom --base-url http://host/v1",
    ),
}

PROVIDER_ORDER = [
    "groq",
    "openrouter",
    "gemini",
    "sambanova",
    "zai",
    "cloudflare",
    "mistral",
    "ollama-cloud",
    "omniroute",
    "ollama",
    "lmstudio",
    "custom",
]


@dataclass(frozen=True)
class CompositeModel:
    id: str
    name: str
    tagline: str
    description: str
    chain: tuple[str, ...]
    system: str
    temperature: float
    purpose: str
    icon: str = "✦"


CODER_SYSTEM = (
    "You are Astra Coder, the coding specialist inside Astra Code. "
    "Be surgical: read before writing, prefer a focused edit over a rewrite, keep diffs minimal. "
    "After changing code, run the most relevant check (tests, linter, type-check, or a tiny repro) "
    "and report the real result. Never invent command output. "
    "If a task is ambiguous, make the smallest reasonable assumption and state it."
)

REASONER_SYSTEM = (
    "You are Astra Reasoner, the architecture and debugging specialist inside Astra Code. "
    "Work in two phases: first build a short, testable plan; then execute it with tools. "
    "Look for root causes instead of symptoms, enumerate failure hypotheses, and verify each one. "
    "Keep the final answer compact: what changed, why, and what was verified. "
    "Use tools when evidence is needed; never fabricate file contents or command results."
)

COMPOSITE_MODELS: dict[str, CompositeModel] = {
    "astra-coder": CompositeModel(
        id="astra-coder",
        name="Astra Coder",
        tagline="код, рефакторинг, тесты",
        description=(
            "Собственная модель Astra: маршрутизирует задачу по лучшим бесплатным кодовым моделям "
            "(Groq GPT-OSS 120B → OpenRouter code models → Gemini 2.5 Pro → SambaNova) "
            "с автоматическим fallback при 429/5xx."
        ),
        chain=("groq", "openrouter", "gemini", "sambanova", "zai", "cloudflare", "mistral", "ollama-cloud", "omniroute", "ollama", "lmstudio", "custom"),
        system=CODER_SYSTEM,
        temperature=0.15,
        purpose="coder",
        icon="⚡",
    ),
    "astra-reasoner": CompositeModel(
        id="astra-reasoner",
        name="Astra Reasoner",
        tagline="архитектура, дебаг, планы",
        description=(
            "Собственная модель Astra: reasoning-first профиль. "
            "Gemini 2.5 Pro → SambaNova DeepSeek → OpenRouter reasoning → Groq GPT-OSS 120B, "
            "с plan-then-act системным промптом."
        ),
        chain=("gemini", "sambanova", "openrouter", "groq", "zai", "cloudflare", "ollama-cloud", "omniroute", "ollama", "lmstudio", "custom"),
        system=REASONER_SYSTEM,
        temperature=0.25,
        purpose="reasoner",
        icon="✧",
    ),
}


# --------------------------------------------------------------------------- helpers


def get_provider(provider_id: str) -> Provider | None:
    return PROVIDERS.get(provider_id)


def resolve_base_url(provider: Provider, cfg: Config) -> str:
    url = provider.base_url
    override = cfg.provider_cfg(provider.id).get("base_url")
    if not override:
        env_name = provider.id.upper().replace("-", "_") + "_BASE_URL"
        override = cfg.option(provider.id, "base_url", env_name)
    if override:
        url = str(override)
    if provider.requires_account_id:
        account = cfg.option("cloudflare", "account_id", "CLOUDFLARE_ACCOUNT_ID")
        if account:
            url = url.replace("{account_id}", account)
    return url.rstrip("/")


def provider_available(provider: Provider, cfg: Config) -> bool:
    if provider.id == "mock":
        return True
    if provider.id == "omniroute":
        return bool(cfg.api_key(provider.id, provider.env_key)) and bool(resolve_base_url(provider, cfg))
    if provider.id == "custom":
        return bool(resolve_base_url(provider, cfg))
    if provider.local:
        return True
    if not cfg.api_key(provider.id, provider.env_key):
        return False
    if provider.requires_account_id and not cfg.option(provider.id, "account_id", "CLOUDFLARE_ACCOUNT_ID"):
        return False
    return True


def missing_setup(provider: Provider, cfg: Config) -> str:
    if provider.id == "omniroute":
        if not cfg.api_key(provider.id, provider.env_key):
            return (
                "нет ключа OmniRoute: astra auth set omniroute --from-omniroute "
                "(или задайте OMNIROUTE_API_KEY)"
            )
        return "сервер OmniRoute не запущен: omniroute serve --daemon --no-open --headless"
    if provider.local:
        if provider.id == "custom":
            return "не задан base_url: astra auth set custom --base-url http://host/v1"
        return f"запустите {provider.short} на {provider.base_url}"
    if not cfg.api_key(provider.id, provider.env_key):
        return f"нет ключа {provider.env_key}: astra auth set {provider.id}"
    if provider.requires_account_id and not cfg.option(provider.id, "account_id", "CLOUDFLARE_ACCOUNT_ID"):
        return "нет account_id: astra auth set cloudflare --account-id ..."
    return "недоступен"


def is_free_model(provider_id: str, model_id: str) -> bool:
    """Conservative free-only check (used unless ``allow_paid`` is on)."""
    if provider_id == "openrouter":
        return model_id.endswith(":free")
    if provider_id == "gemini":
        return model_id.startswith(("gemini-2.5", "gemini-2.0")) or any(
            m.id == model_id for m in GEMINI_MODELS
        )
    if provider_id == "zai":
        return "flash" in model_id.lower()
    if provider_id in {"ollama", "lmstudio", "custom", "mock", "omniroute"}:
        return True
    # Providers whose free tier applies account-wide (no card on file).
    return provider_id in {"groq", "sambanova", "cloudflare", "mistral", "ollama-cloud"}


def free_status(provider_id: str, model_id: str) -> tuple[bool, str]:
    if is_free_model(provider_id, model_id):
        return True, "бесплатно"
    if provider_id == "openrouter":
        return False, "платная модель OpenRouter (нет суффикса :free)"
    if provider_id == "gemini":
        return False, "модель вне бесплатного списка Gemini"
    if provider_id == "zai":
        return False, "платные модели Z.ai (нужен Flash)"
    return False, "не подтверждено, что модель бесплатна"


_SIZE_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d)?)b(?![\da-z])", re.I)


def model_size(model_id: str) -> float:
    match = _SIZE_RE.search(model_id)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


_CODER_HINTS = {
    "code": 7.0,
    "coder": 7.0,
    "devstral": 5.0,
    "laguna": 5.0,
    "north-mini": 5.0,
    "qwen": 3.0,
    "deepseek": 2.5,
    "gpt-oss": 2.5,
    "gemma": 1.5,
    "llama": 1.0,
}
_REASON_HINTS = {
    "reason": 7.0,
    "thinking": 6.0,
    "r1": 6.0,
    "qwq": 6.0,
    "deepseek": 3.0,
    "nemotron": 2.0,
    "pro": 2.0,
    "gpt-oss": 1.5,
}


def score_model(model_id: str, purpose: str, ctx: int = 128_000, preset_rank: int = 0) -> float:
    text = model_id.lower()
    score = 0.0
    hints = _CODER_HINTS if purpose == "coder" else _REASON_HINTS
    for needle, points in hints.items():
        if needle in text:
            score += points
    size = model_size(text)
    if size:
        score += min(size / 20.0, 6.0)
        if size <= 4:
            score -= 4.0
        elif size <= 9:
            score -= 1.0
    for penalty in ("nano", "tiny", "mini", "lite"):
        if penalty in text:
            score -= 2.0
    if "flash" in text:
        score -= 0.5
    score += min(ctx / 200_000.0, 2.0)
    score += max(0.0, 3.0 - preset_rank * 0.4)
    return score


def rank_candidates(
    provider_id: str,
    ids: Iterable[str],
    purpose: str,
    *,
    presets: Iterable[ModelInfo] | None = None,
) -> list[str]:
    preset_list = list(presets if presets is not None else PROVIDERS[provider_id].models)
    preset_rank = {m.id: m.rank for m in preset_list}
    ctx_map = {m.id: m.ctx for m in preset_list}
    unique = list(dict.fromkeys(ids))
    return sorted(
        unique,
        key=lambda mid: (
            -score_model(mid, purpose, ctx_map.get(mid, 128_000), preset_rank.get(mid, 0)),
            preset_rank.get(mid, 99),
            mid,
        ),
    )


def context_window(provider_id: str, model_id: str) -> int:
    provider = PROVIDERS.get(provider_id)
    if provider:
        for model in provider.models:
            if model.id == model_id:
                return model.ctx
    if provider_id == "gemini":
        return 1_048_576
    if provider_id in {"ollama", "lmstudio", "cloudflare"}:
        return 32_768
    return 128_000


def model_label(provider_id: str, model_id: str) -> str:
    provider = PROVIDERS.get(provider_id)
    if provider:
        for model in provider.models:
            if model.id == model_id:
                return model.label
    return model_id


def provider_for_prefix(prefix: str) -> Provider | None:
    return PROVIDERS.get(prefix)


def split_model_ref(ref: str, default_provider: str) -> tuple[str, str]:
    """``groq:openai/gpt-oss-120b`` -> (groq, model); plain id uses default."""
    if ":" in ref:
        prefix, rest = ref.split(":", 1)
        if prefix in PROVIDERS:
            return prefix, rest
    return default_provider, ref


SIGNUP_STEPS: dict[str, tuple[str, list[str]]] = {
    "openrouter": (
        "https://openrouter.ai/keys",
        [
            "Войдите через Google/GitHub (карта и телефон не нужны).",
            "Создайте ключ: Keys → Create Key.",
            "Вставьте его сюда — Astra сохранит ключ в ~/.astra/auth.toml.",
        ],
    ),
    "gemini": (
        "https://aistudio.google.com/apikey",
        [
            "Откройте Google AI Studio и войдите в Google-аккаунт.",
            "Get API key → Create API key (карта не нужна).",
            "Скопируйте ключ AIza… и вставьте сюда.",
        ],
    ),
    "groq": (
        "https://console.groq.com/keys",
        [
            "Зарегистрируйтесь (потребуется телефон).",
            "API Keys → Create API Key.",
            "Скопируйте ключ gsk_… и вставьте сюда.",
        ],
    ),
    "sambanova": (
        "https://cloud.sambanova.ai/keys",
        [
            "Создайте аккаунт SambaNova Cloud.",
            "Не привязывайте карту — free tier работает без неё.",
            "API Keys → Create key.",
        ],
    ),
    "zai": (
        "https://z.ai/manage-apikey/apikey-list",
        [
            "Зарегистрируйтесь на z.ai.",
            "API Keys → Create.",
            "Модели с Flash стоят $0.",
        ],
    ),
    "cloudflare": (
        "https://dash.cloudflare.com/profile/api-tokens",
        [
            "Создайте API token с правом Workers AI: Read.",
            "Скопируйте Account ID со страницы Workers AI.",
            "Astra спросит оба значения.",
        ],
    ),
    "mistral": (
        "https://console.mistral.ai/api-keys",
        [
            "Создайте аккаунт Mistral.",
            "API Keys → Create new key.",
            "Free mode включается в Admin → Billing.",
        ],
    ),
    "ollama-cloud": (
        "https://ollama.com/settings/keys",
        [
            "Создайте аккаунт ollama.com.",
            "Settings → Keys → Create.",
            "Free plan включает стартовые кредиты.",
        ],
    ),
    "omniroute": (
        "https://github.com/diegosouzapw/OmniRoute",
        [
            "Запустите локальный сервер: omniroute serve --daemon --no-open --headless",
            "Astra импортирует ключ из ~/.omniroute/storage.sqlite командой --from-omniroute.",
            "Модели подтянутся из http://127.0.0.1:20128/v1/models.",
        ],
    ),
    "ollama": (
        "https://ollama.com/download",
        [
            "Установите Ollama.",
            "Выполните: ollama pull qwen2.5-coder:14b",
            "Ключ не нужен — сервер работает локально.",
        ],
    ),
    "lmstudio": (
        "https://lmstudio.ai/",
        [
            "Установите LM Studio и скачайте модель.",
            "Developer → Start Server (порт 1234).",
            "Ключ не нужен.",
        ],
    ),
    "custom": (
        "",
        [
            "Укажите base_url OpenAI-совместимого сервера.",
            "Если нужен ключ — задайте CUSTOM_API_KEY.",
            "Модели подтянутся из /models.",
        ],
    ),
}
