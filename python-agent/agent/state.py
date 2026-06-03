from __future__ import annotations
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class BiometricSignal(TypedDict, total=False):
    method: str           # face_recognition | voice | manual
    provider: str         # face-api.js | web_speech_api
    confidence: float     # 0.0-1.0
    voice_verified: bool
    verified_at: int      # unix ts


class VendingState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    biometric: BiometricSignal  # populated by kiosk mode
