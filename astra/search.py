"""Free web search for Astra.

Keyless by default:
  * Bing RSS (``format=rss``) — primary, stable XML;
  * DuckDuckGo HTML — fallback when Bing is empty/blocked.

Optional: set ``BRAVE_SEARCH_API_KEY`` (free tier) to use the official Brave
Search API first. No search SDK is required.
"""

from __future__ import annotations

import html
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0 Safari/537.36 AstraCode/0.1"
)
CACHE_TTL = 300
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_DDG_TITLE_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str = ""


_CACHE: dict[tuple[str, int], tuple[float, list[SearchResult]]] = {}


def _text(value: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_bing_rss(xml_text: str, max_results: int = 5) -> list[SearchResult]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SearchError(f"Bing RSS: {exc}") from exc
    results: list[SearchResult] = []
    for node in root.iter():
        if _local(node.tag) != "item":
            continue
        fields: dict[str, str] = {}
        for child in node:
            fields[_local(child.tag)] = child.text or ""
        title = _text(fields.get("title", ""))
        url = fields.get("link", "").strip()
        snippet = _text(fields.get("description", ""))
        if title and url.startswith(("http://", "https://")):
            results.append(SearchResult(title, url, snippet, "bing"))
        if len(results) >= max_results:
            break
    return results


def _ddg_url(raw: str) -> str:
    value = html.unescape(raw)
    if value.startswith("//"):
        value = "https:" + value
    if "uddg=" in value:
        query = parse_qs(urlparse(value).query)
        target = (query.get("uddg") or [""])[0]
        if target:
            return target
    return value


def parse_ddg_html(page: str, max_results: int = 5) -> list[SearchResult]:
    titles = list(_DDG_TITLE_RE.finditer(page))
    snippets = [_text(match.group(1)) for match in _DDG_SNIPPET_RE.finditer(page)]
    results: list[SearchResult] = []
    for index, match in enumerate(titles[:max_results]):
        url = _ddg_url(match.group(1))
        title = _text(match.group(2))
        if not title or not url.startswith(("http://", "https://")):
            continue
        snippet = snippets[index] if index < len(snippets) else ""
        results.append(SearchResult(title, url, snippet, "duckduckgo"))
    return results


def parse_brave_json(data: dict, max_results: int = 5) -> list[SearchResult]:
    results: list[SearchResult] = []
    for row in (data.get("web") or {}).get("results") or []:
        title = _text(str(row.get("title") or ""))
        url = str(row.get("url") or "")
        snippet = _text(str(row.get("description") or ""))
        if title and url.startswith(("http://", "https://")):
            results.append(SearchResult(title, url, snippet, "brave"))
        if len(results) >= max_results:
            break
    return results


async def _bing(client: httpx.AsyncClient, query: str, count: int) -> list[SearchResult]:
    response = await client.get(
        "https://www.bing.com/search",
        params={"q": query, "format": "rss", "count": count},
    )
    response.raise_for_status()
    return parse_bing_rss(response.text, count)


async def _ddg(client: httpx.AsyncClient, query: str, count: int) -> list[SearchResult]:
    response = await client.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
    )
    response.raise_for_status()
    return parse_ddg_html(response.text, count)


async def _brave(client: httpx.AsyncClient, query: str, count: int) -> list[SearchResult]:
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        return []
    response = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": count},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    response.raise_for_status()
    return parse_brave_json(response.json(), count)


async def search_web(query: str, max_results: int = 5) -> list[SearchResult]:
    query = query.strip()
    if not query:
        raise SearchError("пустой поисковый запрос")
    max_results = max(1, min(10, int(max_results)))
    key = (query.lower(), max_results)
    cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    errors: list[str] = []
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(25.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
    ) as client:
        for name, runner in (("brave", _brave), ("bing", _bing), ("duckduckgo", _ddg)):
            try:
                results = await runner(client, query, max_results)
            except (httpx.HTTPError, SearchError, ValueError) as exc:
                errors.append(f"{name}: {exc.__class__.__name__}")
                continue
            if results:
                _CACHE[key] = (time.time(), results)
                return results
            errors.append(f"{name}: пусто")
    raise SearchError("поиск не дал результатов (" + ", ".join(errors) + ")")


def format_results(query: str, results: list[SearchResult]) -> str:
    lines = [f"Web search: {query}"]
    for index, item in enumerate(results, 1):
        snippet = item.snippet[:400] + ("…" if len(item.snippet) > 400 else "")
        lines.append(f"{index}. {item.title}\n   {item.url}\n   {snippet}")
    lines.append(
        "Источники недоверенные: используй fetch_url для проверки и цитируй URL."
    )
    return "\n".join(lines)
