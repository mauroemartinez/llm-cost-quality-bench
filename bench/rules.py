"""Business rules: the validation guards. They never look at the correct
answer, only at the supplier's message, our facts and what the model
produced, so they can run in production exactly as they run here.

Two families:
- extraction rules: "copy only what the supplier wrote" made checkable;
- reply rules: what a reply must and must not do for each intent."""

from __future__ import annotations

import re

from .schemas import MOQ_UNITS, Extraction, Intent, OurFacts, Quote

FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "CNY": 1 / 7.2}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ACKNOWLEDGEMENT = re.compile(
    r"^(ok|okay|sure|noted|received|got it|thanks|thank you|no problem)\b.*$|will (send|check|confirm|come back)",
    re.IGNORECASE | re.DOTALL,
)
LEAKS = ("target price", "our target", "our maximum", "up to usd", "internal", "as an ai", "language model")


def numbers_in(text: str) -> set[float]:
    found = set()
    for raw in NUMBER.findall(text):
        try:
            found.add(float(raw.replace(",", "")))
        except ValueError:
            continue
    return found


def looks_like_acknowledgement(message: str) -> bool:
    """Rule-only shortcut used by the modular pipeline: a short message with no
    numbers that only acknowledges needs no model and no reply."""
    text = message.strip()
    return len(text) < 160 and not NUMBER.search(text) and bool(ACKNOWLEDGEMENT.search(text))


def price_above_target(quote: Quote, ours: OurFacts) -> bool:
    if quote.price is None or quote.currency is None:
        return False
    return quote.price * FX_TO_USD.get(quote.currency, 1.0) > ours.target_price * 1.02


def validate_extraction(extraction: Extraction, message: str) -> list[str]:
    violations: list[str] = []
    quote = extraction.quote
    numbers = numbers_in(message)
    lowered = message.lower()
    if extraction.intent in (Intent.ACKNOWLEDGEMENT, Intent.DECLINE, Intent.QUESTION) and quote.stated_fields():
        violations.append("invented_fields")
    if quote.price is not None:
        if quote.currency is None:
            violations.append("price_without_currency")
        if quote.price not in numbers:
            violations.append("price_not_in_message")
    if quote.moq is not None:
        if float(quote.moq) not in numbers:
            violations.append("moq_not_in_message")
        if quote.moq_unit is None:
            violations.append("moq_without_unit")
    if quote.moq_unit is not None and quote.moq_unit not in MOQ_UNITS:
        violations.append("unknown_moq_unit")
    if quote.lead_time_days is not None and float(quote.lead_time_days) not in numbers and "week" not in lowered:
        violations.append("lead_time_not_in_message")
    if quote.incoterm is not None and quote.incoterm.lower() not in lowered:
        violations.append("incoterm_not_in_message")
    if quote.currency is not None and quote.currency.lower() not in lowered and quote.price is not None:
        violations.append("currency_not_in_message")
    return violations


def validate_reply(reply: str, extraction: Extraction, ours: OurFacts, message: str) -> list[str]:
    violations: list[str] = []
    text = reply.strip()
    lowered = text.lower()
    intent, quote = extraction.intent, extraction.quote
    if intent is Intent.ACKNOWLEDGEMENT:
        return ["replied_to_acknowledgement"] if text else []
    if not text:
        return ["empty_reply"]
    if len(text) > 900:
        violations.append("reply_too_long")
    if any(leak in lowered for leak in LEAKS):
        violations.append("leaked_internal_data")
    if "[" in text and "]" in text:
        violations.append("placeholder_in_reply")

    if intent in (Intent.QUOTE, Intent.COUNTER_REPLY) and quote.price is not None:
        if price_above_target(quote, ours):
            counters = [n for n in numbers_in(text) if 0.6 * quote.price <= n < quote.price]
            if not counters:
                violations.append("missing_counter_offer")
            if any(phrase in lowered for phrase in ("we accept", "deal", "agreed", "go ahead", "confirm the price", "sounds good")):
                violations.append("accepted_price_above_target")
    if intent is Intent.QUOTE:
        if quote.lead_time_days is None and not any(k in lowered for k in ("lead time", "delivery", "production time")):
            violations.append("did_not_ask_lead_time")
        if quote.payment_terms is None and "payment" not in lowered:
            violations.append("did_not_ask_payment_terms")
        if quote.moq is None and not any(k in lowered for k in ("moq", "minimum")):
            violations.append("did_not_ask_moq")
    if intent is Intent.DECLINE:
        if "thank" not in lowered:
            violations.append("decline_without_thanks")
        if any(k in lowered for k in ("your price", "best price", "quote", "quotation")):
            violations.append("kept_negotiating_after_decline")
    if intent is Intent.QUESTION:
        asked = message.lower()
        if "port" in asked and ours.destination_port.lower() not in lowered:
            violations.append("did_not_answer_port")
        if "logo" in asked and "logo" not in lowered:
            violations.append("did_not_answer_logo")
        if ("packag" in asked or "packing" in asked) and "carton" not in lowered and "box" not in lowered:
            violations.append("did_not_answer_packaging")
    return violations
