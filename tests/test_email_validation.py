import pytest
from unittest.mock import AsyncMock, patch

from core.email_verifier import (
    POPULAR_EMAIL_DOMAINS,
    EmailValidationResult,
    detect_email_typo,
    levenshtein_distance,
    resolve_domain_mx,
    validate_email_syntax,
    verify_email,
    verify_email_list,
)
from connectors.gmail import GmailConnector
from agent.tools import execute_tool
import google_services


# =============================================================================
# 1. Layer 1 Tests: RFC Syntax & Structural Lexer
# =============================================================================

@pytest.mark.parametrize(
    "valid_email",
    [
        "simple@gmail.com",
        "very.common@example.com",
        "disposable.style.email.with+symbol@example.com",
        "other.email-with-hyphen@and.subdomains.org",
        "x@example.com",
        "customer.care@company.co.in",
    ],
)
def test_layer1_syntax_valid_addresses(valid_email):
    ok, err, local, dom = validate_email_syntax(valid_email)
    assert ok is True
    assert err == ""
    assert "@" not in local
    assert "@" not in dom


@pytest.mark.parametrize(
    "invalid_email,expected_err_substring",
    [
        ("", "empty"),
        (None, "empty"),
        ("plainaddress", "exactly one '@'"),
        ("@missingusername.com", "between 1 and 64 characters"),
        ("username@", "between 1 and 253 characters"),
        ("username@.com", "start or end with a dot"),
        ("username@com", "at least one dot"),
        ("username@sub..domain.com", "consecutive dots"),
        (".user@gmail.com", "start or end with a dot"),
        ("user.@gmail.com", "start or end with a dot"),
        ("user..name@gmail.com", "consecutive dots"),
        ("user@domain..com", "consecutive dots"),
        ("user@-domain.com", "start or end with a hyphen"),
        ("user@domain.c", "at least 2 alphabetic characters"),
        ("user@domain.123", "at least 2 alphabetic characters"),
        ("a" * 65 + "@gmail.com", "between 1 and 64 characters"),
        ("user@" + "a" * 250 + ".com", "characters"),
    ],
)
def test_layer1_syntax_invalid_addresses(invalid_email, expected_err_substring):
    ok, err, _, _ = validate_email_syntax(invalid_email)
    assert ok is False
    assert expected_err_substring.lower() in err.lower()


# =============================================================================
# 2. Layer 2 Tests: Typo Detection (Levenshtein Distance)
# =============================================================================

def test_levenshtein_distance_metric():
    assert levenshtein_distance("gmal.com", "gmail.com") == 1
    assert levenshtein_distance("yaho.com", "yahoo.com") == 1
    assert levenshtein_distance("outlok.com", "outlook.com") == 1
    assert levenshtein_distance("gamil.com", "gmail.com") == 2
    assert levenshtein_distance("gmail.com", "gmail.com") == 0


@pytest.mark.parametrize(
    "typo_domain,expected_suggestion",
    [
        ("gmal.com", "gmail.com"),
        ("gamil.com", "gmail.com"),
        ("gmial.com", "gmail.com"),
        ("yaho.com", "yahoo.com"),
        ("outlok.com", "outlook.com"),
        ("hotmial.com", "hotmail.com"),
        ("iclud.com", "icloud.com"),
    ],
)
def test_layer2_typo_detection_finds_correct_suggestion(typo_domain, expected_suggestion):
    has_typo, suggested_dom, suggested_em = detect_email_typo("neeraj", typo_domain)
    assert has_typo is True
    assert suggested_dom == expected_suggestion
    assert suggested_em == f"neeraj@{expected_suggestion}"


def test_layer2_legitimate_domains_never_flagged_as_typo():
    # Popular exact match
    has_typo, _, _ = detect_email_typo("neeraj", "gmail.com")
    assert has_typo is False

    # Legitimate corporate / custom domains
    for corp_dom in ["stripe.com", "airbnb.com", "infosys.com", "microsoft.com", "tatamotors.com"]:
        has_typo, _, _ = detect_email_typo("neeraj", corp_dom)
        assert has_typo is False


# =============================================================================
# 3. Layer 3 Tests: Live DNS MX & Deliverability Resolver
# =============================================================================

@pytest.mark.asyncio
async def test_layer3_resolves_valid_known_providers():
    val = await verify_email("neeraj@gmail.com")
    assert val.is_valid is True
    assert val.syntax_valid is True
    assert val.has_typo is False
    assert val.mx_valid is True
    assert len(val.mx_records) > 0


@pytest.mark.asyncio
async def test_layer3_rejects_nonexistent_domain_nxdomain():
    val = await verify_email("abc@1.gmail.com")
    assert val.is_valid is False
    assert val.mx_valid is False
    assert "does not exist" in (val.error_message or "").lower() or "no active mail exchange" in (val.error_message or "").lower()


@pytest.mark.asyncio
async def test_layer3_rejects_typo_domain_with_no_mx_and_suggests_fix():
    val = await verify_email("neeraj@gmal.com")
    assert val.is_valid is False
    assert val.has_typo is True
    assert val.suggested_email == "neeraj@gmail.com"
    assert "Did you mean 'neeraj@gmail.com'?" in val.error_message


@pytest.mark.asyncio
async def test_layer3_cloudflare_fallback_on_primary_failure():
    # Simulate primary Google DoH failing (HTTP 500), Cloudflare succeeding
    async def mock_cf_get(url, **kwargs):
        class MockResp:
            status_code = 200
            def json(self):
                if "cloudflare" in url:
                    return {
                        "Status": 0,
                        "Answer": [{"name": "corpmail.test.", "type": 15, "data": "10 mail.corpmail.test."}]
                    }
                raise RuntimeError("Google DoH Simulated Down")
        return MockResp()

    with patch("httpx.AsyncClient.get", side_effect=mock_cf_get):
        ok, reason, records = await resolve_domain_mx("corpmail.test", allow_test_domains=False)
        assert ok is True
        assert any("mail.corpmail.test" in r for r in records)


# =============================================================================
# 4. End-to-End Integration Tests (connectors/gmail.py & agent/tools.py)
# =============================================================================

@pytest.mark.asyncio
async def test_gmail_connector_rejects_invalid_and_nonexistent_domains():
    connector = GmailConnector()
    with patch.object(connector, "auth_state", AsyncMock(return_value="connected")):
        # 1. Non-existent domain (1.gmail.com)
        res_nx = await connector.execute(
            12345, "send_email", {"to": "abc@1.gmail.com", "subject": "Hi", "body": "Hello"}
        )
        assert res_nx.ok is False
        assert "does not exist" in res_nx.error["message"].lower() or "no active mail" in res_nx.error["message"].lower()

        # 2. Typo domain (gmal.com)
        res_typo = await connector.execute(
            12345, "send_email", {"to": "test@gmal.com", "subject": "Hi", "body": "Hello"}
        )
        assert res_typo.ok is False
        assert "Did you mean 'test@gmail.com'?" in res_typo.error["message"]


@pytest.mark.asyncio
async def test_gmail_connector_accepts_valid_email_for_confirmation():
    connector = GmailConnector()
    with patch.object(connector, "auth_state", AsyncMock(return_value="connected")):
        res_valid = await connector.execute(
            12345, "send_email", {"to": "aaravmishra619@gmail.com", "subject": "Hi", "body": "Hello"}
        )
        assert res_valid.ok is True
        assert res_valid.data["status"] == "needs_confirmation"
        assert res_valid.data["to"] == "aaravmishra619@gmail.com"


@pytest.mark.asyncio
async def test_agent_tools_execute_tool_send_email_validates_recipient():
    with patch.object(GmailConnector, "auth_state", AsyncMock(return_value="connected")):
        # Invalid domain returns error status
        res = await execute_tool(12345, "send_email", {"to": "user@1.gmail.com", "subject": "Test", "body": "Body"})
        assert res["status"] == "error"
        err_msg = res.get("message") or res.get("details") or res.get("error") or ""
        assert "does not exist" in err_msg.lower() or "no active mail" in err_msg.lower()

        # Valid domain returns needs_confirmation
        res_ok = await execute_tool(12345, "send_email", {"to": "neeraj@gmail.com", "subject": "Test", "body": "Body"})
        assert res_ok["status"] == "needs_confirmation"
        assert res_ok["to"] == "neeraj@gmail.com"


@pytest.mark.asyncio
async def test_google_services_send_email_defense_in_depth():
    with pytest.raises(ValueError) as excinfo:
        await google_services.send_email(12345, "bad@1.gmail.com", "Test", "Body")
    assert "does not exist" in str(excinfo.value).lower() or "no active mail" in str(excinfo.value).lower()
