from __future__ import annotations

from typing import Any

import pytest

from minimal_agent.errors import AgentToolError
from minimal_agent.tools.search import WikipediaSearchProvider
from minimal_agent.tools.weather import OpenMeteoWeatherProvider


class QueueHttpClient:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url: str) -> dict[str, Any]:
        self.urls.append(url)
        return self.responses.pop(0)


def test_wikipedia_provider_normalizes_and_cleans_results():
    client = QueueHttpClient(
        {
            "query": {
                "search": [
                    {
                        "title": "人工智能",
                        "snippet": "<span class=\"searchmatch\">人工智能</span> &amp; Agent",
                        "wordcount": 123,
                    }
                ]
            }
        }
    )

    result = WikipediaSearchProvider(client=client).search(
        "人工智能", limit=1, language="zh"
    )

    assert result["provider"] == "wikipedia"
    assert result["results"][0]["snippet"] == "人工智能 & Agent"
    assert result["results"][0]["url"].startswith("https://zh.wikipedia.org/wiki/")
    assert result["retrieved_at"].endswith("+00:00")
    assert "w/api.php" in client.urls[0]


def test_open_meteo_provider_combines_geocoding_and_forecast():
    client = QueueHttpClient(
        {
            "results": [
                {
                    "name": "上海",
                    "admin1": "上海",
                    "country": "中国",
                    "latitude": 31.22,
                    "longitude": 121.46,
                    "timezone": "Asia/Shanghai",
                }
            ]
        },
        {
            "daily": {
                "time": ["2026-08-11", "2026-08-12"],
                "weather_code": [2, 61],
                "temperature_2m_max": [32.0, 30.0],
                "temperature_2m_min": [27.0, 26.0],
                "precipitation_probability_max": [20, 80],
            }
        },
    )

    result = OpenMeteoWeatherProvider(client=client).forecast(
        "上海", day="tomorrow"
    )

    assert result["provider"] == "open-meteo"
    assert result["location"]["timezone"] == "Asia/Shanghai"
    assert result["forecast"]["date"] == "2026-08-12"
    assert result["forecast"]["condition"] == "雨"
    assert result["forecast"]["precipitation_probability_max"] == 80
    assert "geocoding-api.open-meteo.com" in client.urls[0]
    assert "api.open-meteo.com/v1/forecast" in client.urls[1]


def test_open_meteo_provider_returns_controlled_location_error():
    provider = OpenMeteoWeatherProvider(client=QueueHttpClient({"results": []}))

    with pytest.raises(AgentToolError) as error:
        provider.forecast("不存在的地点")

    assert error.value.code == "location_not_found"
    assert error.value.retryable is False
