from __future__ import annotations
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class VendingState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    # Runtime context passed via config, not stored here
