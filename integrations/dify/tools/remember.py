from collections.abc import Generator
from typing import Any

from client import client, text, user
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from loom import LoomError


class RememberTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        user_id = user(tool_parameters)
        try:
            with client(self.runtime.credentials) as agent:
                value = text(tool_parameters, "value")
                result = agent.call(
                    "memory.set",
                    {"memory_key": text(tool_parameters, "memory_key"), "value": value},
                    user_id=user_id,
                )
        except LoomError:
            raise ValueError(
                "Loom request failed; check the provider connection and token grants"
            ) from None
        yield self.create_json_message(result)
