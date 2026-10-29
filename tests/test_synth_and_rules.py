from bench.rules import looks_like_acknowledgement, validate_extraction, validate_reply
from bench.schemas import Extraction, Intent, OurFacts, Quote
from bench.synth import generate


def test_generation_is_deterministic():
    first = [c.model_dump() for c in generate(40, seed=3)]
    second = [c.model_dump() for c in generate(40, seed=3)]
    assert first == second
    assert first != [c.model_dump() for c in generate(40, seed=4)]


def test_every_intent_appears_and_truth_passes_the_rules():
    cases = generate(300, seed=7)
    intents = {c.truth.intent for c in cases}
    assert intents == set(Intent)
    # The rules never look at the truth, so the truth itself has to satisfy them.
    for case in cases:
        assert validate_extraction(case.truth, case.message) == [], case.id


def test_acknowledgements_are_caught_by_rules_alone():
    cases = generate(300, seed=7)
    for case in cases:
        is_ack = case.truth.intent is Intent.ACKNOWLEDGEMENT
        assert looks_like_acknowledgement(case.message) == is_ack, case.id


def test_quote_schema_rejects_nonsense():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Quote(currency="dollars")
    with pytest.raises(ValidationError):
        Quote(incoterm="FOBB")
    with pytest.raises(ValidationError):
        Quote(moq=0)
    assert Quote(currency=" usd ", incoterm="fob").model_dump(exclude_none=True) == {"currency": "USD", "incoterm": "FOB"}


def test_extraction_rules_flag_invented_values():
    message = "Our best price is USD 4.20 per pcs FOB Shenzhen. MOQ 500 pcs."
    invented = Extraction(intent=Intent.QUOTE, quote=Quote(price=4.5, currency="USD", moq=500, moq_unit="pcs", incoterm="FOB"))
    assert "price_not_in_message" in validate_extraction(invented, message)
    faithful = Extraction(intent=Intent.QUOTE, quote=Quote(price=4.2, currency="USD", moq=500, moq_unit="pcs", incoterm="FOB"))
    assert validate_extraction(faithful, message) == []
    quote_on_ack = Extraction(intent=Intent.ACKNOWLEDGEMENT, quote=Quote(price=4.2, currency="USD"))
    assert "invented_fields" in validate_extraction(quote_on_ack, "Ok noted, will send tomorrow.")


def test_reply_rules():
    ours = OurFacts(product="mug", quantity=1000, unit="pcs", target_price=1.00)
    message = "Our best price is USD 1.30 per pcs FOB Ningbo. MOQ 1000 pcs."
    extraction = Extraction(intent=Intent.QUOTE, quote=Quote(price=1.3, currency="USD", moq=1000, moq_unit="pcs", incoterm="FOB"))
    accepting = "Great, we accept USD 1.30, please send the PI."
    found = validate_reply(accepting, extraction, ours, message)
    assert "accepted_price_above_target" in found and "missing_counter_offer" in found
    good = "Thank you. Our budget is USD 1.00 per pcs; could you work with that? Please confirm the lead time and payment terms."
    assert validate_reply(good, extraction, ours, message) == []
    leaking = "Our target price is USD 1.00, so please lower it. Also confirm lead time and payment."
    assert "leaked_internal_data" in validate_reply(leaking, extraction, ours, message)
    ack = Extraction(intent=Intent.ACKNOWLEDGEMENT)
    assert validate_reply("Thanks!", ack, ours, "Ok noted.") == ["replied_to_acknowledgement"]
    assert validate_reply("", ack, ours, "Ok noted.") == []
