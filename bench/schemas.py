"""The data contracts: what a supplier message is, what the model has to
produce, and what counts as the correct answer. Pydantic does the first
line of validation; business rules live in rules.py."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator

INCOTERMS = ("EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP")
CURRENCIES = ("USD", "EUR", "CNY")
MOQ_UNITS = ("pcs", "sets", "cartons", "kg", "meters", "pairs", "rolls")


class Intent(StrEnum):
    """What the supplier's message is doing."""

    QUOTE = "quote"  # gives prices and terms
    COUNTER_REPLY = "counter_reply"  # answers our counter-offer: accepts, meets halfway or refuses
    QUESTION = "question"  # asks us something before quoting
    DECLINE = "decline"  # cannot supply
    ACKNOWLEDGEMENT = "acknowledgement"  # "ok, will send tomorrow": nothing to answer


class Quote(BaseModel):
    """What the supplier actually stated. Missing means None, never a guess."""

    price: float | None = None
    currency: str | None = None
    moq: int | None = None
    moq_unit: str | None = None
    lead_time_days: int | None = None
    incoterm: str | None = None
    payment_terms: str | None = None

    @field_validator("currency", "incoterm", mode="before")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value).strip().upper()
        return value or None

    @field_validator("currency")
    @classmethod
    def _currency_known(cls, value: str | None) -> str | None:
        if value is not None and value not in CURRENCIES:
            raise ValueError(f"currency must be one of {CURRENCIES}, got {value!r}")
        return value

    @field_validator("incoterm")
    @classmethod
    def _incoterm_known(cls, value: str | None) -> str | None:
        if value is not None and value not in INCOTERMS:
            raise ValueError(f"incoterm must be one of {INCOTERMS}, got {value!r}")
        return value

    @field_validator("moq_unit", mode="before")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = str(value).strip().lower()
        return value or None

    @field_validator("price")
    @classmethod
    def _price_positive(cls, value: float | None) -> float | None:
        if value is not None and not (0 < value < 1_000_000):
            raise ValueError("price must be positive and plausible")
        return value

    @field_validator("moq")
    @classmethod
    def _moq_positive(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("moq must be positive")
        return value

    @field_validator("lead_time_days")
    @classmethod
    def _lead_time_plausible(cls, value: int | None) -> int | None:
        if value is not None and not (1 <= value <= 365):
            raise ValueError("lead_time_days must be between 1 and 365")
        return value

    def stated_fields(self) -> list[str]:
        return [name for name, value in self.model_dump().items() if value is not None]


class Extraction(BaseModel):
    """Stage output: the intent and the quote read from one message."""

    intent: Intent
    quote: Quote = Field(default_factory=Quote)


class ReplyRequirements(BaseModel):
    """What a correct reply has to do, derived when the case is generated.
    These are checked by code, without a model."""

    no_reply: bool = False  # acknowledgements get no answer at all
    must_ask: list[str] = Field(default_factory=list)  # fields the supplier did not state
    must_counter: bool = False  # their price is above our target
    must_answer: str | None = None  # keyword of the fact our answer has to carry
    must_close_politely: bool = False  # declines: thank them, do not keep asking


class OurFacts(BaseModel):
    """What we know about our own order, given to the model as context."""

    product: str
    quantity: int
    unit: str
    target_price: float
    currency: str = "USD"
    destination_port: str = "Buenos Aires"
    logo_printing: bool = False
    packaging: str = "export cartons"


class Case(BaseModel):
    """One synthetic supplier message with its known correct answer."""

    id: str
    supplier: str
    message: str
    ours: OurFacts
    truth: Extraction
    reply: ReplyRequirements
    # Fields with a second reading that is just as correct: the truth holds
    # one, this holds the other, and either scores as right.
    also_accept: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    """Token usage of one call. `exact` is False when we estimated it from text."""

    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    exact: bool = True


class Completion(BaseModel):
    text: str
    usage: Usage
    latency_ms: float
    model: str
    provider: str
    from_cassette: bool = False


class CallRecord(BaseModel):
    """One line of the JSONL ledger: everything we know about one model call."""

    run_id: str
    ts: str
    arch: str
    stage: str
    provider: str
    model: str
    case_id: str
    attempt: int
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    cost_usd: float
    cost_method: str  # "exact" (usage from the API) or "estimated" (from text length)
    latency_ms: float
    validation_passed: bool
    violations: list[str] = Field(default_factory=list)
    fallback: bool = False  # this call went to the big model after the small one failed twice
    from_cassette: bool = False


class CaseResult(BaseModel):
    """What one architecture produced for one case, plus how it got there."""

    arch: str
    case_id: str
    extraction: Extraction | None
    reply_text: str
    calls: int
    retries: int
    fallbacks: int
    violations: list[str]
    cost_usd: float
    latency_ms: float
    parse_failed: bool = False
