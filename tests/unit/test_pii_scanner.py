"""Unit tests for PIIScanner."""

from pathlib import Path

import pytest

from app.services.quarantine.checkers.pii_scanner import PIIScanner


@pytest.fixture
def scanner():
    return PIIScanner()


@pytest.fixture
def default_config():
    return {}


FIXTURES = Path(__file__).parent.parent / "fixtures" / "quarantine"


class TestPIIScanner:
    @pytest.mark.asyncio
    async def test_clean_file_no_findings(self, scanner, default_config):
        """Clean training data should produce no PII findings."""
        findings = await scanner.scan(
            FIXTURES / "training_chat.jsonl", "training_chat.jsonl", default_config
        )
        pii_findings = [f for f in findings if f.code.startswith("pii_")]
        assert len(pii_findings) == 0

    @pytest.mark.asyncio
    async def test_ssn_detected(self, scanner, default_config):
        """Should detect SSN patterns."""
        findings = await scanner.scan(
            FIXTURES / "training_with_pii.jsonl",
            "training_with_pii.jsonl",
            default_config,
        )
        ssn = [f for f in findings if f.code == "pii_ssn"]
        assert len(ssn) == 1
        assert ssn[0].details["count"] == 2
        assert ssn[0].severity == "high"

    @pytest.mark.asyncio
    async def test_email_detected(self, scanner, default_config):
        """Should detect email addresses."""
        findings = await scanner.scan(
            FIXTURES / "training_with_pii.jsonl",
            "training_with_pii.jsonl",
            default_config,
        )
        email = [f for f in findings if f.code == "pii_email"]
        assert len(email) == 1
        assert email[0].details["count"] >= 2
        assert email[0].severity == "medium"

    @pytest.mark.asyncio
    async def test_credit_card_detected(self, scanner, default_config):
        """Should detect credit card numbers that pass Luhn validation."""
        findings = await scanner.scan(
            FIXTURES / "training_with_pii.jsonl",
            "training_with_pii.jsonl",
            default_config,
        )
        cc = [f for f in findings if f.code == "pii_credit_card"]
        assert len(cc) == 1
        assert cc[0].details["count"] >= 1
        assert cc[0].severity == "high"

    @pytest.mark.asyncio
    async def test_phone_detected(self, scanner, default_config):
        """Should detect phone numbers."""
        findings = await scanner.scan(
            FIXTURES / "training_with_pii.jsonl",
            "training_with_pii.jsonl",
            default_config,
        )
        phone = [f for f in findings if f.code == "pii_phone"]
        assert len(phone) == 1
        assert phone[0].details["count"] >= 1
        assert phone[0].severity == "medium"

    @pytest.mark.asyncio
    async def test_mrn_detected(self, scanner, default_config):
        """Should detect medical record numbers."""
        findings = await scanner.scan(
            FIXTURES / "training_with_pii.jsonl",
            "training_with_pii.jsonl",
            default_config,
        )
        mrn = [f for f in findings if f.code == "pii_mrn"]
        assert len(mrn) == 1
        assert mrn[0].details["count"] >= 1
        assert mrn[0].severity == "high"

    @pytest.mark.asyncio
    async def test_dob_detected(self, scanner, default_config):
        """Should detect date of birth patterns."""
        findings = await scanner.scan(
            FIXTURES / "training_with_pii.jsonl",
            "training_with_pii.jsonl",
            default_config,
        )
        dob = [f for f in findings if f.code == "pii_dob"]
        assert len(dob) == 1
        assert dob[0].details["count"] >= 1
        assert dob[0].severity == "medium"

    @pytest.mark.asyncio
    async def test_luhn_validation(self, scanner):
        """Luhn check should reject invalid card numbers and accept valid ones."""
        # Valid: 4539578012184245 (Luhn-valid)
        assert scanner._luhn_check("4539-5780-1218-4245") is True
        # Invalid: too few digits
        assert scanner._luhn_check("1234") is False
        # Invalid: doesn't pass Luhn
        assert scanner._luhn_check("4532-1234-5678-9012") is False
        # Valid: standard test number
        assert scanner._luhn_check("4111111111111111") is True

    @pytest.mark.asyncio
    async def test_plain_text_pii(self, scanner, default_config, tmp_path):
        """Should detect PII in plain text files."""
        text_file = tmp_path / "pii.txt"
        text_file.write_text("My SSN is 123-45-6789\nNothing here\n")
        findings = await scanner.scan(text_file, "pii.txt", default_config)
        ssn = [f for f in findings if f.code == "pii_ssn"]
        assert len(ssn) == 1

    @pytest.mark.asyncio
    async def test_empty_file_handled(self, scanner, default_config, tmp_path):
        """Empty file should return no findings without crashing."""
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        findings = await scanner.scan(empty_file, "empty.txt", default_config)
        assert findings == []
