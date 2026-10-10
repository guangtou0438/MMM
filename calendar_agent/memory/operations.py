from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


def strip_json_fences(raw):
    """Remove outer Markdown fence lines, never extract or repair JSON content.

    GLM can append a closing fence without an opener. Extra objects, prose,
    truncated JSON and invalid operations still go through strict validation.
    """
    value = raw.strip()
    value = re.sub(r'\A`{3,}(?:json)?[ \t]*\r?\n', '', value, flags=re.I)
    return re.sub(r'\r?\n`{3,}[ \t]*\Z', '', value).strip()


class MemoryOperation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['ADD', 'UPDATE', 'MERGE', 'SPLIT', 'PROMOTE', 'DEMOTE', 'EXPIRE', 'INVALIDATE', 'CONFLICT', 'NOOP']
    source_ids: list[str] = Field(min_length=1, max_length=20)
    targets: dict[str, int] = Field(default_factory=dict, description='UPDATE/MERGE/SPLIT/PROMOTE/DEMOTE/EXPIRE/INVALIDATE 必须提供已有记忆 ID 与当前版本；首次新增使用 ADD。')
    content: str = Field(default='', max_length=6000)
    kind: Literal['profile', 'preference', 'episodic', 'task', 'procedural'] = 'episodic'
    confidence: float = Field(default=.8, ge=0, le=1)
    certainty: Literal['reported', 'inferred', 'uncertain'] = 'reported'
    sensitivity: Literal['normal', 'sensitive'] = 'normal'
    fragments: list[str] = Field(default_factory=list, max_length=10)
    expires: float | None = None

    @model_validator(mode='after')
    def require_existing_targets(self):
        if self.operation in {'UPDATE', 'MERGE', 'SPLIT', 'PROMOTE', 'DEMOTE', 'EXPIRE', 'INVALIDATE'} and not self.targets:
            raise PydanticCustomError('memory_target_required', '此操作必须提供已有记忆 ID 与版本；首次新增应使用 ADD')
        return self
