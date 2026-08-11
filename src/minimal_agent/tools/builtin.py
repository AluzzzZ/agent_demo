from __future__ import annotations

import ast
import operator
from typing import Any

from .registry import ToolContext, ToolDefinition, ToolRegistry
from .search import SearchProvider, WikipediaSearchProvider
from .weather import OpenMeteoWeatherProvider, WeatherProvider


_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _calculate_node(node: ast.AST, depth: int = 0) -> int | float:
    if depth > 20:
        raise ValueError("expression is too deeply nested")
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            raise ValueError("booleans are not numbers")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_calculate_node(node.operand, depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left = _calculate_node(node.left, depth + 1)
        right = _calculate_node(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent is too large")
        result = _BINARY_OPS[type(node.op)](left, right)
        if abs(result) > 1e100:
            raise ValueError("result is too large")
        return result
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def calculator(arguments: dict[str, Any], _: ToolContext) -> dict[str, Any]:
    expression = arguments["expression"].strip()
    if not expression or len(expression) > 200:
        raise ValueError("expression must contain 1-200 characters")
    tree = ast.parse(expression, mode="eval")
    result = _calculate_node(tree.body)
    return {"expression": expression, "result": result}


def todo(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
    action = arguments["action"]
    if action == "add":
        if "title" not in arguments:
            raise ValueError("title is required when action is add")
        return {"todo": context.store.add_todo(
            context.user_id, context.session_id, arguments["title"]
        )}
    if action == "list":
        return {"todos": context.store.list_todos(context.user_id, context.session_id)}
    if action == "complete":
        if "todo_id" not in arguments:
            raise ValueError("todo_id is required when action is complete")
        return {"todo": context.store.complete_todo(
            context.user_id, context.session_id, arguments["todo_id"]
        )}
    raise ValueError(f"unsupported todo action: {action}")


def create_default_registry(
    *,
    search_provider: SearchProvider | None = None,
    weather_provider: WeatherProvider | None = None,
) -> ToolRegistry:
    search_backend = search_provider or WikipediaSearchProvider()
    weather_backend = weather_provider or OpenMeteoWeatherProvider()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="calculator",
            description="安全计算一个只包含数字、括号和基本算术运算符的表达式。",
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
                "additionalProperties": False,
            },
            handler=calculator,
        )
    )
    registry.register(
        ToolDefinition(
            name="search",
            description=(
                "使用免费的 Wikipedia/MediaWiki API 搜索百科知识，返回标题、摘要和地址。"
                "它不覆盖整个互联网；需要百科事实或背景资料时使用。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                    "language": {"type": "string", "enum": ["zh", "en"]},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda arguments, _: search_backend.search(
                arguments["query"].strip(),
                limit=arguments.get("limit", 3),
                language=arguments.get("language", "zh"),
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="weather",
            description=(
                "使用免费的 Open-Meteo API 查询城市今天或明天的真实天气预报。"
                "结果包含最高/最低温度、天气状况和最大降水概率。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "minLength": 1},
                    "day": {"type": "string", "enum": ["today", "tomorrow"]},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
            handler=lambda arguments, _: weather_backend.forecast(
                arguments["city"].strip(), day=arguments.get("day", "today")
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="todo",
            description=(
                "管理当前窗口独立的待办。action=add 时传 title；"
                "action=complete 时传 todo_id；action=list 无需额外参数。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list", "complete"]},
                    "title": {"type": "string", "minLength": 1, "maxLength": 500},
                    "todo_id": {"type": "integer", "minimum": 1},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=todo,
        )
    )
    return registry
