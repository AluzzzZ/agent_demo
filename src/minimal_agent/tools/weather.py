from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlencode

from ..errors import AgentToolError, ToolUpstreamError
from ..http_client import JsonHttpClient


class WeatherProvider(Protocol):
    def forecast(self, city: str, *, day: str = "today") -> dict[str, Any]: ...


@dataclass
class OpenMeteoWeatherProvider:
    """Keyless Open-Meteo geocoding + daily forecast provider."""

    client: JsonHttpClient = field(default_factory=JsonHttpClient)

    def forecast(self, city: str, *, day: str = "today") -> dict[str, Any]:
        geo_params = urlencode(
            {"name": city, "count": 1, "language": "zh", "format": "json"}
        )
        geocoding = self.client.get(
            f"https://geocoding-api.open-meteo.com/v1/search?{geo_params}"
        )
        candidates = geocoding.get("results")
        if not isinstance(candidates, list) or not candidates:
            raise AgentToolError(
                "location_not_found", f"没有找到地点“{city}”，请补充城市或行政区。"
            )
        location = candidates[0]
        if not isinstance(location, dict):
            raise ToolUpstreamError("天气地理编码返回了无效地点。", retryable=False)

        latitude = location.get("latitude")
        longitude = location.get("longitude")
        timezone_name = str(location.get("timezone") or "auto")
        forecast_params = urlencode(
            {
                "latitude": latitude,
                "longitude": longitude,
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max"
                ),
                "timezone": timezone_name,
                "forecast_days": 2,
            }
        )
        forecast = self.client.get(
            f"https://api.open-meteo.com/v1/forecast?{forecast_params}"
        )
        daily = forecast.get("daily")
        if not isinstance(daily, dict):
            raise ToolUpstreamError("天气 API 缺少 daily 数据。", retryable=False)
        index = 1 if day == "tomorrow" else 0
        try:
            weather_code = int(daily["weather_code"][index])
            result = {
                "date": daily["time"][index],
                "weather_code": weather_code,
                "condition": _weather_condition(weather_code),
                "temperature_max_c": daily["temperature_2m_max"][index],
                "temperature_min_c": daily["temperature_2m_min"][index],
                "precipitation_probability_max": daily[
                    "precipitation_probability_max"
                ][index],
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ToolUpstreamError("天气 API 的预报数据不完整。", retryable=False) from exc
        return {
            "provider": "open-meteo",
            "city": city,
            "day": day,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "location": {
                "name": location.get("name"),
                "admin1": location.get("admin1"),
                "country": location.get("country"),
                "latitude": latitude,
                "longitude": longitude,
                "timezone": timezone_name,
            },
            "forecast": result,
            "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)",
        }


def _weather_condition(code: int) -> str:
    if code == 0:
        return "晴"
    if code in {1, 2, 3}:
        return "少云到多云"
    if code in {45, 48}:
        return "雾"
    if code in {51, 53, 55, 56, 57}:
        return "毛毛雨"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "雨"
    if code in {71, 73, 75, 77, 85, 86}:
        return "雪"
    if code in {95, 96, 99}:
        return "雷暴"
    return "未知"
