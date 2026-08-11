from __future__ import annotations

import ast
import operator
from typing import Any

from .registry import ToolContext, ToolDefinition, ToolRegistry


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


_SEARCH_DOCUMENTS = [
    {
        "title": "Agent Loop 设计笔记",
        "snippet": "模型决定调用工具或结束，Runtime 负责执行并回填结果。",
        "url": "mock://docs/agent-loop",
        "keywords": "agent loop runtime 工具 循环",
    },
    {
        "title": "Session 隔离指南",
        "snippet": "使用 user_id 与 session_id 复合键隔离窗口状态。",
        "url": "mock://docs/session-isolation",
        "keywords": "session 会话 窗口 sqlite 隔离",
    },
    {
        "title": "Context 压缩策略",
        "snippet": "保留最近消息，将较早历史压缩为结构化摘要。",
        "url": "mock://docs/context-compaction",
        "keywords": "context 上下文 压缩 summary memory",
    },
    {
        "title": "工具注册机制",
        "snippet": "工具包含名称、描述、JSON Schema 与执行函数。",
        "url": "mock://docs/tool-registry",
        "keywords": "tool schema registry 注册 json",
    },
]


def search(arguments: dict[str, Any], _: ToolContext) -> dict[str, Any]:
    query = arguments["query"].strip().lower()
    limit = arguments.get("limit", 3)
    tokens = [token for token in query.replace("-", " ").split() if token]
    scored: list[tuple[int, dict[str, str]]] = []
    for document in _SEARCH_DOCUMENTS:
        haystack = " ".join(document.values()).lower()
        score = sum(token in haystack for token in tokens)
        if score:
            scored.append((score, document))
    scored.sort(key=lambda item: (-item[0], item[1]["title"]))
    selected = scored[:limit] or [(0, item) for item in _SEARCH_DOCUMENTS[:limit]]
    return {
        "query": arguments["query"],
        "mock": True,
        "results": [
            {key: value for key, value in document.items() if key != "keywords"}
            for _, document in selected
        ],
    }


_WEATHER = {
    "上海": {"today": (30, "多云", 65), "tomorrow": (31, "阵雨", 72)},
    "北京": {"today": (28, "晴", 38), "tomorrow": (29, "晴转多云", 41)},
    "深圳": {"today": (32, "雷阵雨", 78), "tomorrow": (31, "中雨", 82)},
}


def weather(arguments: dict[str, Any], _: ToolContext) -> dict[str, Any]:
    city = arguments["city"].strip()
    day = arguments.get("day", "today")
    city_data = _WEATHER.get(city)
    if city_data is None:
        # Stable mock fallback keeps demos deterministic for arbitrary cities.
        city_data = {"today": (26, "多云", 55), "tomorrow": (27, "晴", 50)}
    temperature, condition, humidity = city_data[day]
    return {
        "city": city,
        "day": day,
        "temperature_c": temperature,
        "condition": condition,
        "humidity_percent": humidity,
        "mock": True,
    }


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


def create_default_registry() -> ToolRegistry:
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
            description="搜索本地 mock 文档，返回确定性的标题、摘要和地址。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=search,
        )
    )
    registry.register(
        ToolDefinition(
            name="weather",
            description="查询城市今天或明天的 mock 天气，适合稳定演示和测试。",
            input_schema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "minLength": 1},
                    "day": {"type": "string", "enum": ["today", "tomorrow"]},
                },
                "required": ["city"],
                "additionalProperties": False,
            },
            handler=weather,
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

