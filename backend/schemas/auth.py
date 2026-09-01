"""Auth request and response shapes (Doc 11 §2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """
    🔴 `email` is a plain string, not `EmailStr`.

    Sign-in matches against a value already in the database; it does not
    create one. Validating the format here adds nothing — a malformed address
    simply matches no row — and it actively harms: `EmailStr` rejects
    `.local` as a special-use domain, which locked out every seeded account
    (`agent@agricrm.local`, `ops@agricrm.local`) the first time this was run
    against real data.

    Strict validation belongs where an address is accepted for the first time,
    not where one is looked up.
    """

    email: str
    password: str


class TokenPair(BaseModel):
    access: str
    refresh: str


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    district_ids: list[int]
    is_cross_territory: bool
    mfa_enforced: bool
    mfa_satisfied: bool = Field(
        description="Whether the token making this request has cleared the second factor."
    )

    model_config = {"from_attributes": True}


class LoginResponse(TokenPair):
    user: UserOut
    mfa_required: bool
    mfa_enrolled: bool


class RefreshRequest(BaseModel):
    refresh: str


class LogoutRequest(BaseModel):
    refresh: str


class MFAVerifyRequest(BaseModel):
    token: str = Field(min_length=6, max_length=8)


class MFAEnrolResponse(BaseModel):
    provisioning_uri: str = Field(
        description="otpauth:// URI. Contains the shared secret — never log it."
    )
    secret: str = Field(description="Base32 secret, for manual entry.")
    qr_svg: str = Field(description="Inline SVG QR code for the provisioning URI.")
    already_confirmed: bool


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, description="Doc 12 §13: 12 characters minimum.")
