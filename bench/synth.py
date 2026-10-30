"""Synthetic supplier messages with a known correct answer.

Everything here is invented: suppliers, products, prices and wording. The
generator is seeded, so the same seed always yields the same cases, and it
adds the noise real messages have: an alternative Incoterm offered next to
the real price, a price revised inside the same message, ranges, "about
three weeks", missing fields, and the occasional typo."""

from __future__ import annotations

import json
import random
from pathlib import Path

from .schemas import Case, Extraction, Intent, OurFacts, Quote, ReplyRequirements

# Fixed rates used to compare a foreign-currency quote with our USD target.
# The same table is used by the rules, so the bench stays consistent.
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "CNY": 1 / 7.2}

PRODUCTS: list[tuple[str, str, float]] = [
    ("LED desk lamp with USB charging base", "pcs", 4.20),
    ("stainless steel water bottle 750 ml", "pcs", 2.80),
    ("PPR pipe fusion welder, gas heated", "sets", 215.00),
    ("memory foam pillow 60 x 40 cm", "pcs", 3.60),
    ("3D embroidered cotton cap", "pcs", 1.95),
    ("foldable camping chair", "pcs", 9.80),
    ("polyester fabric 150 gsm", "meters", 0.85),
    ("ceramic coffee mug 350 ml", "pcs", 1.10),
    ("wireless optical mouse", "pcs", 3.30),
    ("rubber yoga mat 6 mm", "pcs", 5.40),
    ("LED strip light 5 m", "rolls", 2.60),
    ("aluminium laptop stand", "pcs", 6.90),
    ("children's rain boots", "pairs", 4.70),
    ("plastic storage box 40 l", "pcs", 3.15),
    ("digital kitchen scale", "pcs", 2.95),
    ("cotton tote bag", "pcs", 0.72),
    ("bluetooth speaker", "pcs", 7.40),
    ("stainless steel cutlery set", "sets", 4.10),
    ("garden hose 20 m", "pcs", 5.90),
    ("hex key set", "sets", 1.45),
]

SUPPLIERS = [
    "Brightway Electronics Co., Ltd.",
    "Jinhai Household Products",
    "Sunrise Textile Mill",
    "Golden Harbor Trading",
    "Lantern Hill Industrial",
    "Evergreen Homeware Factory",
    "Northstar Tools Manufacture",
    "Blue Pavilion Lighting",
    "Silver Crane Plastics",
    "Redwood Outdoor Gear",
]

PORTS = ["Shenzhen", "Ningbo", "Shanghai", "Qingdao", "Xiamen"]
PAYMENT_TERMS = [
    "30% T/T deposit, 70% before shipment",
    "50% deposit, balance against copy of B/L",
    "100% T/T in advance",
    "L/C at sight",
    "30% deposit, 70% T/T before loading",
]
GREETINGS = ["Dear friend,", "Hello,", "Hi,", "Dear Sir/Madam,", "Hello dear,"]
SIGNOFFS = ["Best regards", "Thanks and regards", "Kind regards", "Waiting for your reply", "Have a nice day"]

INTENT_WEIGHTS = [
    (Intent.QUOTE, 45),
    (Intent.COUNTER_REPLY, 20),
    (Intent.ACKNOWLEDGEMENT, 15),
    (Intent.QUESTION, 12),
    (Intent.DECLINE, 8),
]


def _money(value: float) -> str:
    return f"{value:.2f}"


def _typos(rng: random.Random, text: str) -> str:
    swaps = [("please", "pls"), ("the ", "teh "), ("quantity", "quantiy"), ("delivery", "delivey"), ("before", "befor")]
    rng.shuffle(swaps)
    for old, new in swaps[:2]:
        if old in text:
            text = text.replace(old, new, 1)
    return text


def _lead_time(rng: random.Random) -> tuple[str, int]:
    """A lead-time sentence and the number the rules expect (upper bound of a range)."""
    kind = rng.random()
    if kind < 0.45:
        days = rng.choice([15, 20, 25, 30, 35, 45])
        return rng.choice([f"Delivery {days} days after deposit.", f"Lead time {days} days.", f"Production needs {days} days."]), days
    if kind < 0.8:
        low = rng.choice([15, 20, 25, 30])
        high = low + rng.choice([5, 10])
        return rng.choice([f"Lead time {low}-{high} days.", f"Delivery time {low} to {high} days after deposit."]), high
    weeks = rng.choice([3, 4, 5, 6])
    return f"Production time about {weeks} weeks.", weeks * 7


def _quote_message(rng: random.Random, product: str, unit: str, list_price: float, ours: OurFacts) -> tuple[str, Quote, ReplyRequirements]:
    currency = rng.choices(["USD", "EUR", "CNY"], weights=[80, 10, 10])[0]
    price_in_currency = round(list_price / FX_TO_USD[currency], 2)
    incoterm = rng.choices(["FOB", "EXW", "CIF", "CFR", "FCA"], weights=[55, 15, 15, 10, 5])[0]
    port = rng.choice(PORTS)
    lines = [rng.choice(GREETINGS), f"Thanks for your inquiry about the {product}."]

    price = price_in_currency
    revised = rng.random() < 0.2
    alternative = rng.random() < 0.3
    if revised:
        first = round(price * rng.uniform(1.05, 1.12), 2)
        price = price_in_currency
        lines.append(
            rng.choice(
                [
                    f"Our price is {currency} {_money(first)} per {unit} {incoterm} {port}. "
                    f"After checking with my manager we can do {currency} {_money(price)} for you.",
                    f"Normally {currency} {_money(first)}/{unit} {incoterm}. "
                    f"For this order I can offer {currency} {_money(price)} per {unit}.",
                ]
            )
        )
    else:
        lines.append(
            rng.choice(
                [
                    f"Our best price is {currency} {_money(price)} per {unit} {incoterm} {port}.",
                    f"{incoterm} {port} price: {currency} {_money(price)}/{unit}.",
                    f"Unit price {_money(price)} {currency} ({incoterm} {port}).",
                ]
            )
        )
    if alternative:
        alt_price = round(price * rng.uniform(1.18, 1.35), 2)
        lines.append(f"If you prefer, we can also offer DDP to your warehouse at {currency} {_money(alt_price)} per {unit}.")

    quote = Quote(price=price, currency=currency, incoterm=incoterm)
    missing: list[str] = []

    if rng.random() < 0.8:
        moq = rng.choice([100, 200, 300, 500, 1000, 2000, 3000, 5000])
        quote.moq, quote.moq_unit = moq, unit
        lines.append(rng.choice([f"MOQ is {moq} {unit}.", f"Minimum order {moq} {unit}.", f"MOQ: {moq} {unit} per colour."]))  # noqa: E501
    else:
        missing.append("moq")

    if rng.random() < 0.75:
        sentence, days = _lead_time(rng)
        quote.lead_time_days = days
        lines.append(sentence)
    else:
        missing.append("lead_time")

    if rng.random() < 0.75:
        terms = rng.choice(PAYMENT_TERMS)
        quote.payment_terms = terms
        lines.append(rng.choice([f"Payment: {terms}.", f"Payment terms {terms}.", f"We accept {terms}."]))
    else:
        missing.append("payment_terms")

    lines.append(
        rng.choice(
            ["Please confirm the quantity so we can prepare the PI.", "Looking forward to your order.", "Any question please let me know."]
        )
    )
    lines.append(rng.choice(SIGNOFFS))
    message = "\n".join(lines)
    if rng.random() < 0.15:
        message = _typos(rng, message)

    above_target = price * FX_TO_USD[currency] > ours.target_price * 1.02
    reply = ReplyRequirements(must_ask=missing, must_counter=above_target)
    return message, quote, reply


def _counter_reply_message(
    rng: random.Random, product: str, unit: str, list_price: float, ours: OurFacts
) -> tuple[str, Quote, ReplyRequirements, dict]:
    """The supplier answers a counter-offer we made at our target price (always in USD)."""
    target = ours.target_price
    incoterm = rng.choice(["FOB", "EXW", "CIF"])
    kind = rng.random()
    lines = [rng.choice(GREETINGS)]
    quantity_stated = False
    if kind < 0.5:
        price = target
        accepted = rng.choice(
            [
                f"Ok, we accept USD {_money(target)} per {unit} {incoterm} for {ours.quantity} {unit}.",
                f"After discussion with the factory we can accept your price USD {_money(target)} {incoterm}.",
            ]
        )
        lines.append(accepted)
        # When the acceptance names the quantity ("for 1000 rolls"), that is
        # the size of this order, not the supplier's minimum. Leaving the
        # minimum empty and reading the quantity into it are both fair, so
        # the case accepts either.
        quantity_stated = " for " in accepted
        lines.append(rng.choice(["Please confirm so we prepare the proforma invoice.", "Shall we go ahead with the PI?"]))
        reply = ReplyRequirements(must_ask=["proforma invoice"])
    elif kind < 0.8:
        price = round((target + list_price) / 2, 2)
        lines.append(
            rng.choice(
                [
                    f"We cannot do USD {_money(target)}, but we can meet you halfway at USD {_money(price)} per {unit} {incoterm}.",
                    f"USD {_money(target)} is too low for us. Best we can do is USD {_money(price)} {incoterm}.",
                ]
            )
        )
        reply = ReplyRequirements(must_counter=True)
    else:
        price = round(list_price * rng.uniform(0.97, 1.0), 2)
        lines.append(
            rng.choice(
                [
                    f"Sorry, USD {_money(target)} is below our cost. Our best price is USD {_money(price)} per {unit} {incoterm}.",
                    f"We checked again and cannot reach USD {_money(target)}. The lowest is USD {_money(price)} {incoterm}.",
                ]
            )
        )
        reply = ReplyRequirements(must_counter=True)
    lines.append(rng.choice(SIGNOFFS))
    quote = Quote(price=price, currency="USD", incoterm=incoterm)
    also_accept = {"moq": ours.quantity, "moq_unit": unit} if quantity_stated else {}
    return "\n".join(lines), quote, reply, also_accept


def _question_message(rng: random.Random, product: str, ours: OurFacts) -> tuple[str, ReplyRequirements]:
    topic = rng.choice(["port", "logo", "packaging"])
    lines = [rng.choice(GREETINGS), f"Thanks for your inquiry about the {product}. Before quoting we need to confirm something."]
    if topic == "port":
        lines.append(rng.choice(["Which is your destination port?", "Please advise the port of discharge so we calculate freight."]))
        must_answer = ours.destination_port
    elif topic == "logo":
        lines.append(rng.choice(["Do you need your logo printed on the product?", "Should we add your logo, or plain without printing?"]))
        must_answer = "logo"
    else:
        lines.append(rng.choice(["What packaging do you need, export cartons or colour box?", "Please confirm the packing requirement."]))
        must_answer = "carton"
    lines.append(rng.choice(SIGNOFFS))
    return "\n".join(lines), ReplyRequirements(must_answer=must_answer)


def _decline_message(rng: random.Random, product: str) -> str:
    lines = [
        rng.choice(GREETINGS),
        rng.choice(
            [
                f"Sorry, we do not produce the {product}. We only make related items.",
                "Sorry, your order quantity is too small for our factory, we cannot supply this time.",
                f"Unfortunately we cannot supply the {product} at the moment, our line is fully booked.",
            ]
        ),
        rng.choice(["Hope to cooperate in the future.", "Thanks for understanding."]),
        rng.choice(SIGNOFFS),
    ]
    return "\n".join(lines)


def _acknowledgement_message(rng: random.Random) -> str:
    return rng.choice(
        [
            "Ok noted, will send the quotation tomorrow.",
            "Received, thanks. I will check with the factory and come back to you.",
            "Sure, no problem.",
            "Got it, I will send the catalogue later today.",
            "Ok, let me confirm with my manager first.",
        ]
    )


def generate(n: int = 300, seed: int = 7) -> list[Case]:
    rng = random.Random(seed)
    cases: list[Case] = []
    intents, weights = zip(*INTENT_WEIGHTS, strict=True)
    for i in range(n):
        product, unit, list_price = rng.choice(PRODUCTS)
        quantity = rng.choice([500, 1000, 2000, 3000, 5000])
        ours = OurFacts(
            product=product,
            quantity=quantity,
            unit=unit,
            target_price=round(list_price * rng.uniform(0.75, 0.95), 2),
            destination_port=rng.choice(["Buenos Aires", "Montevideo", "Valparaiso", "Santos"]),
            logo_printing=rng.random() < 0.5,
        )
        intent = rng.choices(intents, weights=weights)[0]
        supplier = rng.choice(SUPPLIERS)
        also_accept: dict = {}
        if intent is Intent.QUOTE:
            message, quote, reply = _quote_message(rng, product, unit, list_price, ours)
        elif intent is Intent.COUNTER_REPLY:
            message, quote, reply, also_accept = _counter_reply_message(rng, product, unit, list_price, ours)
        elif intent is Intent.QUESTION:
            message, reply = _question_message(rng, product, ours)
            quote = Quote()
        elif intent is Intent.DECLINE:
            message, quote, reply = _decline_message(rng, product), Quote(), ReplyRequirements(must_close_politely=True)
        else:
            message, quote, reply = _acknowledgement_message(rng), Quote(), ReplyRequirements(no_reply=True)
        cases.append(
            Case(
                id=f"case-{i:04d}",
                supplier=supplier,
                message=message,
                ours=ours,
                truth=Extraction(intent=intent, quote=quote),
                reply=reply,
                also_accept=also_accept,
            )
        )
    return cases


def save(cases: list[Case], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case.model_dump(mode="json"), ensure_ascii=False) + "\n")


def load(path: Path) -> list[Case]:
    with path.open(encoding="utf-8") as handle:
        return [Case.model_validate(json.loads(line)) for line in handle if line.strip()]
