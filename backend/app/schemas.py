from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    session_id: str
    intent: int
    response: str
    data: dict | None = None


class NewSessionResponse(BaseModel):
    session_id: str
    message: str
