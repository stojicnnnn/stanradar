"""Tests for the Claude scoring call.

These do not hit the API. They assert the request shape, because the failure mode worth
guarding against is a silently wrong request - a mistyped parameter, images dropped, or
the photo cap ignored - which in production shows up only as a bad score or a 400.
"""

from unittest.mock import AsyncMock, patch

import anthropic
import pytest
from pydantic import ValidationError

from stanradar.config import LlmConfig
from stanradar.llm import Grader, Verdict
from stanradar.models import Listing


def listing() -> Listing:
    return Listing(
        source="fourzida",
        external_id="abc",
        url="https://example.com/abc",
        title="Dvosoban stan",
        description="Renoviran stan na Vračaru.",
        price_eur=700,
        area_m2=60.0,
        rooms=2.0,
        floor=3,
        total_floors=5,
        district="Kalenić",
        municipality="Vračar",
        images=[f"https://img.example/{i}.jpg" for i in range(9)],
        is_agency=False,
    )


def grader(**overrides) -> Grader:
    cfg = LlmConfig(enabled=True, **overrides)
    with patch("anthropic.AsyncAnthropic"):
        return Grader(cfg)


def _response(verdict: Verdict):
    response = AsyncMock()
    response.parsed_output = verdict
    response.stop_reason = "end_turn"
    return response


VERDICT = Verdict(
    fit=78,
    verdict="Honest, recently renovated two-room flat in a good part of Vracar.",
    condition="renovated",
    pros=["renovated kitchen"],
    cons=["small bathroom"],
)


@pytest.mark.asyncio
async def test_the_request_carries_images_text_and_the_configured_model():
    g = grader(model="claude-opus-5-5", effort="low", max_images=4)
    g.client.messages.parse = AsyncMock(return_value=_response(VERDICT))

    result = await g.grade(listing(), "- Budget: 400-900 EUR")

    assert result.fit == 78
    kwargs = g.client.messages.parse.call_args.kwargs
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["output_format"] is Verdict

    content = kwargs["messages"][0]["content"]
    images = [b for b in content if b["type"] == "image"]
    texts = [b for b in content if b["type"] == "text"]
    # max_images caps the photos, even though the listing has nine.
    assert len(images) == 4
    assert all(b["source"]["type"] == "url" for b in images)
    assert len(texts) == 1
    # The prompt must carry both the numbers and the user's stated preferences.
    assert "700 EUR/month" in texts[0]["text"]
    assert "Budget: 400-900 EUR" in texts[0]["text"]
    assert "Renoviran stan" in texts[0]["text"]


@pytest.mark.asyncio
async def test_extra_notes_reach_the_prompt():
    g = grader(notes="I need space for a desk.")
    g.client.messages.parse = AsyncMock(return_value=_response(VERDICT))
    await g.grade(listing(), "- Budget: 400-900 EUR")
    text = g.client.messages.parse.call_args.kwargs["messages"][0]["content"][-1]["text"]
    assert "I need space for a desk." in text


@pytest.mark.asyncio
async def test_api_errors_return_none_instead_of_breaking_the_poll_loop():
    g = grader()
    # anthropic 2.x is built on httpx2, so the request object must come from there.
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com")
    g.client.messages.parse = AsyncMock(
        side_effect=anthropic.APIConnectionError(request=request)
    )
    assert await g.grade(listing(), "prefs") is None


@pytest.mark.asyncio
async def test_a_refusal_returns_none():
    g = grader()
    refused = AsyncMock()
    refused.stop_reason = "refusal"
    refused.parsed_output = None
    g.client.messages.parse = AsyncMock(return_value=refused)
    assert await g.grade(listing(), "prefs") is None


def test_the_verdict_schema_bounds_the_score():
    # The 0-100 bound is what the alert threshold compares against, so it must hold.
    with pytest.raises(ValidationError):
        Verdict(fit=120, verdict="x", condition="good")
    with pytest.raises(ValidationError):
        Verdict(fit=-1, verdict="x", condition="good")
