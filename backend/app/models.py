from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Transaction(StrictModel):
    id: str
    card_last4: str = Field(pattern=r'^\d{4}$')
    txn_date: date
    post_date: date
    merchant: str = Field(max_length=200)
    amount: Decimal = Field(max_digits=16, decimal_places=2, allow_inf_nan=False)
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    amount_aed: Decimal = Field(max_digits=16, decimal_places=2, allow_inf_nan=False)
    reference: str = Field(min_length=1, max_length=100)


class Line(StrictModel):
    gl_account: str
    cost_center: str | None = None
    amount: Decimal = Field(max_digits=16, decimal_places=2, allow_inf_nan=False)
    currency: Literal['AED'] = 'AED'


class Proposal(StrictModel):
    txn_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=2000)
    lines: list[Line] = Field(min_length=2, max_length=100)

    @model_validator(mode='after')
    def balanced(self):
        if sum(line.amount for line in self.lines) != 0:
            raise ValueError('Journal entry must balance exactly')
        if any(line.amount == 0 for line in self.lines):
            raise ValueError('Zero-value lines are not allowed')
        if len(set(self.txn_ids)) != len(self.txn_ids):
            raise ValueError('Duplicate transaction ids')
        return self


class Decision(StrictModel):
    decision: Literal['approve', 'reject']
    comment: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def rejection_comment(self):
        if self.decision == 'reject' and not self.comment.strip():
            raise ValueError('Rejection requires a comment')
        return self
