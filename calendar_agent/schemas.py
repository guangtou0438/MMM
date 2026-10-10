from typing import Annotated
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StringConstraints

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManualSubtask(Input):
    title: Title
    slot_start_utc: AwareDatetime | None = None
    duration_min: Annotated[StrictInt, Field(gt=0)]
    # Manual create accepts 0-based indices; translated to persisted IDs.
    depends_on: Annotated[StrictInt, Field(ge=0)] | None = None


class TaskCreate(Input):
    title: Title
    deadline_utc: AwareDatetime
    estimated_minutes: Annotated[StrictInt, Field(gt=0)]
    subtasks: list[ManualSubtask] = Field(default_factory=list, max_length=100)


class ResolveTimes(Input):
    values: list[Annotated[str, StringConstraints(max_length=40)]] = Field(min_length=1, max_length=101)


class ChatInput(Input):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
    conversation_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$")] | None = None
    request_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f-]{36}$")] | None = None
    legacy_task_id: Annotated[str, StringConstraints(max_length=80)] | None = None


class NoteInput(Input):
    content: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
