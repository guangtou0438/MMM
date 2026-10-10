from typing import Literal
from ..schemas import Input
from pydantic import Field, StrictInt
from typing import Annotated

class SessionStart(Input):
    subtask_id: Annotated[StrictInt, Field(gt=0)]

class SessionComplete(Input):
    session_id: Annotated[StrictInt, Field(gt=0)]
    actual_minutes: Annotated[StrictInt, Field(ge=0)]
    outcome: Literal['completed', 'partial', 'blocked']
    note: str = Field(default='', max_length=6000)
