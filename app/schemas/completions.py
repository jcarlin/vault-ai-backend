from pydantic import BaseModel, Field


class CompletionRequest(BaseModel):
    model: str
    prompt: str | list[str]
    stream: bool = False
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float = Field(default=1.0, ge=0, le=1)
    stop: str | list[str] | None = None
    echo: bool = False
    suffix: str | None = None


class CompletionChoice(BaseModel):
    index: int
    text: str
    finish_reason: str | None = None


class CompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CompletionResponse(BaseModel):
    id: str
    object: str = "text_completion"
    created: int
    model: str
    choices: list[CompletionChoice]
    usage: CompletionUsage | None = None
