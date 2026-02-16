from pydantic import BaseModel


class ActivityItem(BaseModel):
    id: str
    type: str  # training, upload, inference, user, system
    title: str
    description: str
    timestamp: str
    user: str | None = None


class ActivityFeed(BaseModel):
    items: list[ActivityItem]
    total: int
