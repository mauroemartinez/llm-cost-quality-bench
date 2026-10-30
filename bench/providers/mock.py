"""A simulated provider: no model behind it. It reads the supplier message
with regular expressions, like a weak model would, and makes mistakes at a
rate that depends on the "model": mock-big is careful, mock-small is sloppy.
Its only purpose is to exercise the pipeline (tests, CI, dry runs) and to
show what the guards catch. Results from it are SIMULATED and every report
says so. Never quote them as model results."""

from __future__ import annotations

import hashlib
import json
import random
import re

from ..pricing import estimate_tokens
from ..schemas import INCOTERMS, Completion, Intent, Usage
from .base import completion

PRICE = re.compile(r"(USD|EUR|CNY)\s?(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?(USD|EUR|CNY)")
MOQ = re.compile(r"(?:MOQ|minimum order)[^\d]{0,12}(\d[\d,]*)\s*(pcs|sets|cartons|kg|meters|pairs|rolls)?", re.IGNORECASE)
DAYS = re.compile(r"(\d+)(?:\s*(?:-|to)\s*(\d+))?\s*days", re.IGNORECASE)
WEEKS = re.compile(r"(\d+)\s*weeks", re.IGNORECASE)
TARGET = re.compile(r"willing to pay: (USD|EUR|CNY) (\d+(?:\.\d+)?)")
PORT = re.compile(r"Destination port: ([^.]+)\.")
LOGO = re.compile(r"Logo printing: (yes|no)")
ERROR_RATE = {"mock-big": 0.03, "mock-small": 0.18}


def _prices(message: str) -> list[tuple[str, float, int]]:
    found = []
    for match in PRICE.finditer(message):
        currency = match.group(1) or match.group(4)
        value = float(match.group(2) or match.group(3))
        found.append((currency, value, match.start()))
    return found


def _main_price(message: str) -> tuple[str, float] | None:
    """The last price that is not an alternative ("also offer DDP ... at")."""
    candidates = [(c, v) for c, v, pos in _prices(message) if "also offer" not in message[max(0, pos - 60) : pos].lower()]
    return candidates[-1] if candidates else None


def _intent(message: str) -> Intent:
    low = message.lower()
    if not re.search(r"\d", message) and re.search(r"\b(ok|noted|received|sure|got it|will send|come back)\b", low):
        return Intent.ACKNOWLEDGEMENT
    declining = any(k in low for k in ("do not produce", "cannot supply", "too small", "fully booked"))
    if ("sorry" in low or "unfortunately" in low) and declining:
        return Intent.DECLINE
    if re.search(r"accept (your price|usd)", low) or any(
        k in low for k in ("meet you halfway", "below our cost", "cannot reach", "too low for us", "cannot do usd")
    ):
        return Intent.COUNTER_REPLY
    if ("?" in message or "before quoting" in low or "please confirm" in low or "please advise" in low) and not _prices(message):
        return Intent.QUESTION
    return Intent.QUOTE


def _extract(message: str, rng: random.Random, sloppy: float) -> dict:
    quote: dict = {k: None for k in ("price", "currency", "moq", "moq_unit", "lead_time_days", "incoterm", "payment_terms")}
    price = _main_price(message)
    if rng.random() < sloppy:
        prices = _prices(message)
        price = (prices[-1][0], prices[-1][1]) if prices else price  # grabs the alternative when there is one
    if price:
        quote["currency"], quote["price"] = price
    moq = MOQ.search(message)
    if moq:
        quote["moq"] = int(moq.group(1).replace(",", ""))
        quote["moq_unit"] = (moq.group(2) or "pcs").lower()
        if rng.random() < sloppy:
            quote["moq_unit"] = None
    days = DAYS.search(message)
    weeks = WEEKS.search(message)
    if days:
        quote["lead_time_days"] = int(days.group(2) or days.group(1))
    elif weeks:
        quote["lead_time_days"] = int(weeks.group(1)) * 7
    for code in INCOTERMS:
        if re.search(rf"\b{code}\b", message):
            quote["incoterm"] = code
            break
    for sentence in re.split(r"(?<=[.!])\s+|\n", message):
        if any(k in sentence for k in ("T/T", "L/C", "%", "Payment", "advance")) and not re.search(r"\b(days|weeks)\b", sentence):
            quote["payment_terms"] = re.sub(r"^(Payment terms|Payment:|We accept)\s*", "", sentence.strip().rstrip(".")).strip()
            break
    return quote


def _reply(message: str, user: str, intent: Intent, quote: dict, rng: random.Random, sloppy: float) -> str:
    if intent is Intent.ACKNOWLEDGEMENT:
        return "Thanks, noted!" if rng.random() < sloppy else ""
    target = TARGET.search(user)
    port = PORT.search(user)
    logo = LOGO.search(user)
    if intent is Intent.DECLINE:
        return "Thank you for letting us know. We will keep your factory in mind for future orders. Best regards"
    if intent is Intent.QUESTION:
        low = message.lower()
        if "packag" in low or "packing" in low:
            return "Hello, standard export cartons are fine. Please send your best quotation. Best regards"
        if "logo" in low:
            answer = "yes, please include our logo" if logo and logo.group(1) == "yes" else "no logo, plain product"
            return f"Hello, {answer}. Please send your best quotation. Best regards"
        return f"Hello, the destination port is {port.group(1) if port else 'Buenos Aires'}. Please send your best quotation. Best regards"
    price, currency = quote.get("price"), quote.get("currency") or "USD"
    target_value = float(target.group(2)) if target else 0.0
    fx = {"USD": 1.0, "EUR": 1.08, "CNY": 1 / 7.2}[currency]
    above = price is not None and price * fx > target_value * 1.02
    lines = ["Hello, thank you for your offer."]
    if above:
        if rng.random() < sloppy:
            lines.append(f"Great, we accept {currency} {price:.2f}, please send the PI.")
        else:
            counter = round(target_value / fx, 2)
            lines.append(f"Our budget for this order is {currency} {counter:.2f} per unit; could you work with that?")
    elif intent is Intent.COUNTER_REPLY:
        lines.append("Confirmed, please send the proforma invoice.")
    if intent is Intent.QUOTE:
        asks = []
        if quote.get("moq") is None:
            asks.append("the MOQ")
        if quote.get("lead_time_days") is None and rng.random() >= sloppy:
            asks.append("the lead time")
        if quote.get("payment_terms") is None:
            asks.append("the payment terms")
        if asks:
            lines.append("Please also confirm " + ", ".join(asks) + ".")
    lines.append("Best regards")
    return " ".join(lines)


class MockProvider:
    name = "mock"

    def complete(self, model: str, system: str, user: str, *, max_tokens: int = 400, temperature: float | None = 0.0) -> Completion:
        task = system.splitlines()[0].replace("TASK:", "").strip()
        seed = hashlib.sha256(f"{model}|{task}|{user}".encode()).hexdigest()
        rng = random.Random(seed)
        sloppy = ERROR_RATE.get(model, 0.1)
        corrected = "broke these rules" in user
        body = user.split("\n\nYour previous answer broke", 1)[0]
        message = body.split("Supplier message:\n", 1)[-1].split("\nWhat we read", 1)[0]
        if corrected:
            sloppy = 0.0  # the correction usually lands
        intent = _intent(message)
        if rng.random() < sloppy and intent is Intent.ACKNOWLEDGEMENT:
            intent = Intent.QUOTE
        if task == "classify":
            payload = {"intent": intent.value}
        elif task == "extract":
            payload = {"quote": _extract(message, rng, sloppy)}
        elif task == "reply":
            given = body.split("What we read from the message (JSON): ", 1)
            parsed = json.loads(given[1].splitlines()[0]) if len(given) == 2 else {"intent": intent.value, "quote": {}}
            payload = {"reply": _reply(message, user, Intent(parsed["intent"]), parsed.get("quote") or {}, rng, sloppy)}
        else:
            quote = _extract(message, rng, sloppy) if intent in (Intent.QUOTE, Intent.COUNTER_REPLY) else {}
            payload = {"intent": intent.value, "quote": quote, "reply": _reply(message, user, intent, quote, rng, sloppy)}
        text = json.dumps(payload)
        system_tokens = estimate_tokens(system)
        usage = Usage(
            input_tokens=system_tokens + estimate_tokens(user),
            cached_tokens=int(system_tokens * 0.8),  # the fixed part of the prompt is served from cache
            output_tokens=estimate_tokens(text),
            exact=True,
        )
        latency = rng.gauss(1900 if model == "mock-big" else 650, 180)
        return completion(self.name, model, text, usage, max(120.0, latency))
