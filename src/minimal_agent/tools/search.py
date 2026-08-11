from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from ..http_client import JsonHttpClient


class SearchProvider(Protocol):
    def search(
        self, query: str, *, limit: int = 3, language: str = "zh"
    ) -> dict[str, Any]: ...


@dataclass
class WikipediaSearchProvider:
    """Free, keyless full-text search backed by Wikimedia's MediaWiki API."""

    client: JsonHttpClient = field(default_factory=JsonHttpClient)

    def search(self, query: str, *, limit: int = 3, language: str = "zh") -> dict[str, Any]:
        language = language if language in {"zh", "en"} else "zh"
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        params = urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
                "srnamespace": 0,
                "format": "json",
                "utf8": 1,
            }
        )
        payload = self.client.get(f"{endpoint}?{params}")
        raw_results = payload.get("query", {}).get("search", [])
        results: list[dict[str, Any]] = []
        if isinstance(raw_results, list):
            for item in raw_results[:limit]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title", "")).strip()
                if not title:
                    continue
                results.append(
                    {
                        "title": title,
                        "snippet": _clean_snippet(str(item.get("snippet", ""))),
                        "url": f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                        "word_count": item.get("wordcount"),
                    }
                )
        return {
            "provider": "wikipedia",
            "language": language,
            "query": query,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "results": results,
            "notice": "免费 Wikimedia/MediaWiki 全文检索，仅覆盖 Wikipedia 内容。",
        }


def _clean_snippet(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value)
    return " ".join(html.unescape(without_tags).split())[:600]
