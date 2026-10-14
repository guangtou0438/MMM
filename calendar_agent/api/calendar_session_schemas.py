"""Strict contracts for date tasks and user actions; no model permission flags."""
from datetime import date
from typing import Annotated, Literal
from pydantic import Field, StrictInt, StringConstraints
from ..schemas import Input

PositiveInt = Annotated[StrictInt, Field(gt=0)]
Nonempty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class AgendaItem(Input):
    text: str = Field(min_length=1, max_length=1000)
    done: bool = False

class CalendarSessionCreate(Input):
    activity_type: Literal["task", "arrangement"] | None = None
    request_id: str = Field(min_length=1, max_length=80)
    title: Nonempty = Field(max_length=300)
    session_goal: str = Field(default='', max_length=6000)
    planned_date: date | None = None
    deadline_date: date | None = None
    requires_deadline: bool = False
    agenda: list[AgendaItem] = Field(default_factory=list, max_length=50)
    duplicate_confirmation_id: PositiveInt | None = None

class RecurringTaskCreate(Input):
    """Weekly repetition expanded into ordinary dated tasks (max 60 occurrences)."""
    activity_type: Literal["task", "arrangement"] | None = None
    title: Nonempty = Field(max_length=300)
    session_goal: str = Field(default='', max_length=6000)
    time_text: str = Field(default='', max_length=60, description='每次的时段，如 18:00-21:00')
    weekdays: list[Literal[1, 2, 3, 4, 5, 6, 7]] = Field(min_length=1, max_length=7, description='每周重复的星期，周一为 1，周日为 7')
    start_date: date | None = Field(default=None, description='首次允许日期，默认今天')
    until_date: date | None = Field(default=None, description='重复截止日期（含），距开始不超过 366 天；用户没说结束时间（如长期习惯）就不要填，默认排未来 8 周')

class SessionAction(Input):
    request_id: str = Field(min_length=1, max_length=80)
    version: PositiveInt

class TrashEmpty(Input):
    request_id: str = Field(min_length=1, max_length=80)
    session_ids: list[PositiveInt] = Field(min_length=1, max_length=500)

class CalendarSessionComplete(SessionAction):
    pass

class SessionUpdate(SessionAction):
    activity_type: Literal["task", "arrangement"] | None = None
    title: Nonempty | None = Field(default=None, max_length=300)
    session_goal: str | None = Field(default=None, max_length=6000)
    planned_date: date | None = None
    deadline_date: date | None = None
    agenda: list[AgendaItem] | None = Field(default=None, max_length=50)

class ChatInput(Input):
    home_context_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")
    request_id: str = Field(min_length=1, max_length=80)
    content: Nonempty = Field(max_length=16000)
    version: PositiveInt | None = None

class SuggestionDecision(Input):
    request_id: str = Field(min_length=1, max_length=80)
    action: Literal['accept', 'ignore']
    proposal: dict | None = None

class Proposal(Input):
    activity_type: Literal["task", "arrangement"] | None = None
    kind: Literal['create', 'update', 'archive', 'restore']
    target_session_id: PositiveInt | None = None
    version: PositiveInt | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    session_goal: str | None = Field(default=None, max_length=6000)
    date_text: str | None = Field(default=None, max_length=100)
    deadline_text: str | None = Field(default=None, max_length=100)
    requires_deadline: bool = False
    agenda: list[AgendaItem] | None = Field(default=None, max_length=50)

class MemoryAction(Input):
    kind: Literal['forget', 'correct', 'restore']
    source_ids: list[PositiveInt] = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=3000)

class AssistantOutput(Input):
    reply: str = Field(min_length=1, max_length=16000)
    proposals: list[Proposal] = Field(default_factory=list, max_length=10)
    memory_actions: list[MemoryAction] = Field(default_factory=list, max_length=5)
    citation_ids: list[PositiveInt] = Field(default_factory=list, max_length=12)
