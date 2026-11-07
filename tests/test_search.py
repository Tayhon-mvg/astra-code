from __future__ import annotations

from astra.search import format_results, parse_bing_rss, parse_brave_json, parse_ddg_html


def test_parse_bing_rss():
    xml = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>First &amp; result</title>
        <link>https://example.com/one</link>
        <description>&lt;b&gt;Snippet&lt;/b&gt; text</description>
      </item>
      <item>
        <title>Second</title>
        <link>https://example.com/two</link>
        <description>Other</description>
      </item>
    </channel></rss>"""
    results = parse_bing_rss(xml, 2)
    assert len(results) == 2
    assert results[0].title == "First & result"
    assert results[0].url == "https://example.com/one"
    assert results[0].snippet == "Snippet text"
    assert results[0].source == "bing"


def test_parse_ddg_html():
    page = """
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdoc">A &amp; B</a>
    <a class="result__snippet">Useful <b>snippet</b></a>
    <a rel="nofollow" class="result__a" href="https://example.org/page">Second</a>
    <a class="result__snippet">Other snippet</a>
    """
    results = parse_ddg_html(page, 2)
    assert len(results) == 2
    assert results[0].title == "A & B"
    assert results[0].url == "https://example.com/doc"
    assert results[0].snippet == "Useful snippet"
    assert results[1].url == "https://example.org/page"


def test_parse_brave_json_and_format():
    results = parse_brave_json(
        {"web": {"results": [{"title": "Brave", "url": "https://brave.example", "description": "Desc"}]}},
        1,
    )
    assert results[0].source == "brave"
    text = format_results("query", results)
    assert "https://brave.example" in text
    assert "недоверенные" in text
