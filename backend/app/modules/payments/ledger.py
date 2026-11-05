"""Double-entry ledger. Every transaction must sum to zero (also enforced by a
deferred constraint trigger in the database)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LedgerEntry


class UnbalancedTransaction(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    account: str
    amount_minor: int  # signed: debit > 0, credit < 0


async def post_txn(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    lines: list[Line],
    currency: str,
    memo: str,
    order_id: uuid.UUID | None,
) -> uuid.UUID:
    if len(lines) < 2:
        raise UnbalancedTransaction("a transaction needs at least two lines")
    if any(line.amount_minor == 0 for line in lines):
        raise UnbalancedTransaction("zero-amount lines are not allowed")
    if sum(line.amount_minor for line in lines) != 0:
        raise UnbalancedTransaction("transaction does not balance")
    txn_id = uuid.uuid4()
    await db.execute(
        insert(LedgerEntry),
        [
            {
                "tenant_id": tenant_id,
                "txn_id": txn_id,
                "account": line.account,
                "amount_minor": line.amount_minor,
                "currency": currency,
                "memo": memo,
                "order_id": order_id,
            }
            for line in lines
        ],
    )
    return txn_id
