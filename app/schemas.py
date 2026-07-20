"""Pydantic models for the NID extraction API contract and Gemini schema."""

import re
from datetime import date
from typing import Self

from pydantic import BaseModel, Field, field_validator, model_validator

_NID_CLEAN_PATTERN = re.compile(r"[\s-]+")
_VALID_NID_LENGTHS = (10, 13, 17)
_BLOOD_GROUP_SPACE_PATTERN = re.compile(r"\s+")
_VALID_BLOOD_GROUPS = frozenset({"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"})


class NIDData(BaseModel):
    """Public API response schema for extracted Bangladesh NID card data."""

    name: str | None = None
    fatherName: str | None = None
    motherName: str | None = None
    dateOfBirth: str | None = None
    bloodGroup: str | None = None
    placeOfBirth: str | None = None
    nidNumber: str | None = None
    presentAddress: str | None = None
    permanentAddress: str | None = None
    issueDate: str | None = None

    @field_validator("nidNumber", mode="before")
    @classmethod
    def validate_nid_number(cls, value: str | None) -> str | None:
        """Strip whitespace/hyphens and require exactly 10, 13, or 17 digits."""
        if value is None:
            return None
        cleaned = _NID_CLEAN_PATTERN.sub("", value)
        if not cleaned.isdigit() or len(cleaned) not in _VALID_NID_LENGTHS:
            return None
        return cleaned

    @field_validator("dateOfBirth", "issueDate", mode="before")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        """Accept only strings that parse as an ISO YYYY-MM-DD date."""
        if value is None:
            return None
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return value

    @field_validator("bloodGroup", mode="before")
    @classmethod
    def validate_blood_group(cls, value: str | None) -> str | None:
        """Normalize blood group text and null it if not a recognized group."""
        if value is None:
            return None
        cleaned = _BLOOD_GROUP_SPACE_PATTERN.sub("", value.strip()).upper()
        cleaned = cleaned.replace("−", "-")
        if cleaned not in _VALID_BLOOD_GROUPS:
            return None
        return cleaned

    @model_validator(mode="after")
    def mirror_single_address(self) -> Self:
        """Mirror a single printed address into both address fields.

        Many Bangladesh NID layouts print only one address on the card, but
        the model is sometimes inconsistent about which field (present or
        permanent) it places that value in, leaving the other null even
        when only one address ever existed. To make behavior deterministic:
        if exactly one of ``presentAddress``/``permanentAddress`` is set,
        mirror it into the other; if both or neither are set, leave them
        unchanged.
        """
        if self.presentAddress is not None and self.permanentAddress is None:
            self.permanentAddress = self.presentAddress
        elif self.permanentAddress is not None and self.presentAddress is None:
            self.presentAddress = self.permanentAddress
        return self


class GeminiNIDExtraction(NIDData):
    """Gemini structured-output schema.

    Extends the public NIDData contract with two internal fields used only to
    validate the extraction quality (whether the images are a Bangladesh NID
    card and whether they were readable); these are never exposed in the
    public API response.
    """

    name: str | None = Field(
        default=None,
        description=(
            "Full name in English, romanized per Bangladeshi conventions, "
            "e.g. 'মোঃ' -> 'Md.'"
        ),
    )
    fatherName: str | None = Field(
        default=None, description="Father's full name, translated to English."
    )
    motherName: str | None = Field(
        default=None, description="Mother's full name, translated to English."
    )
    dateOfBirth: str | None = Field(
        default=None, description="Date of birth normalized to YYYY-MM-DD."
    )
    bloodGroup: str | None = Field(
        default=None,
        description="Blood group exactly as printed, e.g. 'B+', 'O-', 'AB+'.",
    )
    placeOfBirth: str | None = Field(
        default=None,
        description="Place of birth (usually a district), translated to English.",
    )
    nidNumber: str | None = Field(
        default=None, description="NID number: 10, 13, or 17 digits, digits only."
    )
    presentAddress: str | None = Field(
        default=None, description="Present address, translated to English."
    )
    permanentAddress: str | None = Field(
        default=None, description="Permanent address, translated to English."
    )
    issueDate: str | None = Field(
        default=None, description="Card issue date normalized to YYYY-MM-DD."
    )
    is_nid: bool = Field(
        description="Whether the images actually appear to be a Bangladesh NID card."
    )
    readability_issue: str | None = Field(
        default=None,
        description="Short description if the images are blurry or unreadable.",
    )

    def to_public(self) -> NIDData:
        """Build the public NIDData response from only the ten public fields."""
        return NIDData(
            name=self.name,
            fatherName=self.fatherName,
            motherName=self.motherName,
            dateOfBirth=self.dateOfBirth,
            bloodGroup=self.bloodGroup,
            placeOfBirth=self.placeOfBirth,
            nidNumber=self.nidNumber,
            presentAddress=self.presentAddress,
            permanentAddress=self.permanentAddress,
            issueDate=self.issueDate,
        )


class ErrorResponse(BaseModel):
    """Standard error response shape used for all error conditions."""

    error: str
