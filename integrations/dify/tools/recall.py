from collections.abc import Generator
from typing import Any

from client import client, limit, text, user
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from loom import LoomError


class RecallTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        user_id = user(tool_parameters)
        try:
            with client(self.runtime.credentials) as agent:
                result = agent.call(
                    "memory.search",
                    {
                        "query": text(tool_parameters, "query"),
                        "top_k": limit(tool_parameters),
                        "namespace_scope": "this",
                        "bodies": "full",
                        "explain": False,
                    },
                    user_id=user_id,
                    agent_id="*",
                )
        except LoomError:
            raise ValueError(
                "Loom request failed; check the provider connection and token grants"
            ) from None
        yield self.create_json_message(result)
