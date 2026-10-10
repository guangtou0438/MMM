from typing import Annotated, Literal
from pydantic import AwareDatetime, Field, StrictInt, model_validator
from ..schemas import Input, Title


class Goal(Input):
    title: Title
    deadline: AwareDatetime
    estimated_hours: float = Field(gt=0, allow_inf_nan=False)


class PlannedSubtask(Input):
    title: Title
    slot_start: AwareDatetime
    duration_min: Annotated[StrictInt, Field(gt=0)]
    depends_on: Annotated[StrictInt, Field(ge=0)] | None


class PlanOutput(Input):
    intent: Literal["create_plan", "add_task", "query", "clarify"]
    clarify_question: str | None
    goal: Goal | None
    subtasks: list[PlannedSubtask] = Field(max_length=14)
    reasoning: str = Field(max_length=3000)

    @model_validator(mode="after")
    def coherent(self):
        if self.intent in ("create_plan", "add_task"):
            if not self.goal or not self.subtasks or self.clarify_question:
                raise ValueError("创建计划必须有 goal/subtasks，clarify_question 必须为 null")
        else:
            if self.goal is not None or self.subtasks:
                raise ValueError("query/clarify 不能包含计划")
            if self.intent == "clarify" and not (self.clarify_question or "").strip():
                raise ValueError("clarify 必须提出具体问题")
        return self
