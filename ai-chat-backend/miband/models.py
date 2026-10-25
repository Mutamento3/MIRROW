import math
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Sample(BaseModel):
    model_config = ConfigDict(extra='forbid')
    measured_at: float
    raw_kind: int = Field(default=0, ge=-1, le=255)
    intensity: int = Field(default=0, ge=0, le=255)
    steps: int = Field(default=0, ge=0, le=1000)
    heart_rate: int = Field(default=0, ge=0, le=255)
    unknown: int = Field(default=0, ge=0, le=255)
    sleep: int = Field(default=0, ge=0, le=255)
    deep_sleep: int = Field(default=0, ge=0, le=255)
    rem_sleep: int = Field(default=0, ge=0, le=255)
    sleep_stage: Literal['', 'light', 'deep', 'rem', 'awake'] = ''

    @field_validator('measured_at')
    @classmethod
    def timestamp_valid(cls, value):
        if not math.isfinite(value) or not time.time()-32*86400 <= value <= time.time()+300:
            raise ValueError('measurement time outside supported sync window')
        return value


class Batch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    device_name: str = Field(default='Xiaomi Smart Band 7', max_length=80)
    samples: list[Sample] = Field(min_length=1, max_length=500)
