"""Success-fee calculation. The fee is only ever based on money actually recovered."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.money import ZERO, percentage_of, round_cents


@dataclass(frozen=True)
class FeeBreakdown:
    recovered_amount: Decimal
    success_fee_percentage: Decimal
    success_fee: Decimal
    client_receives: Decimal


def validate_fee_percentage(percentage: Decimal) -> Decimal:
    if percentage < ZERO or percentage > Decimal(100):
        raise ValueError("success fee percentage must be between 0 and 100")
    return percentage


def calculate_success_fee(recovered_amount: Decimal | None, percentage: Decimal) -> FeeBreakdown:
    validate_fee_percentage(percentage)
    recovered = round_cents(recovered_amount or ZERO)
    if recovered < ZERO:
        raise ValueError("recovered amount cannot be negative")
    fee = percentage_of(recovered, percentage)
    return FeeBreakdown(
        recovered_amount=recovered,
        success_fee_percentage=percentage,
        success_fee=fee,
        client_receives=recovered - fee,
    )
