from pydantic import BaseModel


class UsageDataPoint(BaseModel):
    date: str
    requests: int
    tokens: int


class ResponseTimeDistribution(BaseModel):
    range: str
    count: int


class ModelUsageStats(BaseModel):
    model: str
    requests: int
    percentage: float


class InsightsResponse(BaseModel):
    usage_history: list[UsageDataPoint]
    response_time_distribution: list[ResponseTimeDistribution]
    model_usage: list[ModelUsageStats]
    total_requests: int
    total_tokens: int
    avg_response_time: float
    active_users: int
