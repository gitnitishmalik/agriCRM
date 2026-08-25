"""
Auth endpoints (Doc 11 §2).

Every view carries an explicit @extend_schema. drf-spectacular cannot infer
request/response shapes from a plain APIView, and an inferred `{}` becomes an
`any` in the generated TypeScript client — which defeats the reason Doc 03 §4
made TypeScript non-negotiable on a 60-table domain model.
"""

from __future__ import annotations

from django_otp import devices_for_user
from django_otp.plugins.otp_totp.models import TOTPDevice
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import (
    HealthSerializer,
    LoginSerializer,
    LogoutRequestSerializer,
    MFAEnrolResponseSerializer,
    MFAVerifySerializer,
    PasswordChangeSerializer,
    TokenPairSerializer,
    UserSerializer,
)


@extend_schema(
    summary="Log in",
    description="Returns a token pair plus the user's role, territory and MFA state.",
    tags=["auth"],
)
class LoginView(TokenObtainPairView):
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_scope = "user"


class MeView(APIView):
    """Current user, role, permissions and territory."""

    @extend_schema(
        summary="Current user",
        responses=UserSerializer,
        tags=["auth"],
    )
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class MFAVerifyView(APIView):
    """
    Verify a TOTP code and re-issue a token pair with mfa_satisfied=true.

    The re-issue matters: without it the client keeps a token whose claim says
    MFA is outstanding, and every protected call keeps failing.
    """

    @extend_schema(
        summary="Verify MFA code",
        request=MFAVerifySerializer,
        responses={
            200: TokenPairSerializer,
            400: OpenApiResponse(description="Invalid or expired code"),
        },
        tags=["auth"],
    )
    def post(self, request):
        serializer = MFAVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["token"]

        # Confirmed devices first — that is the everyday sign-in path.
        #
        # Then unconfirmed ones, which is how enrolment completes: /mfa/enrol/
        # creates the device unconfirmed, and proving you can read a code off
        # it is exactly what confirms the authenticator holds the right secret.
        # Checking only confirmed devices makes enrolment impossible to finish,
        # because nothing else ever flips the flag.
        for confirmed in (True, False):
            for device in devices_for_user(request.user, confirmed=confirmed):
                if not device.verify_token(code):
                    continue

                if not device.confirmed:
                    device.confirmed = True
                    device.save(update_fields=["confirmed"])

                refresh = RefreshToken.for_user(request.user)
                refresh["role"] = request.user.role
                refresh["mfa_required"] = True
                refresh["mfa_satisfied"] = True
                return Response({"access": str(refresh.access_token), "refresh": str(refresh)})

        return Response(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Invalid MFA code.",
                    "details": {},
                    "request_id": getattr(request, "request_id", None),
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )


class MFAEnrolView(APIView):
    """
    Begin TOTP enrolment.

    The device is created unconfirmed and stays that way until the user proves
    they can read a code off it, which /mfa/verify/ handles. Returns a QR code
    to scan and the raw secret for anyone entering it by hand.
    """

    @extend_schema(
        summary="Begin MFA enrolment",
        request=None,
        responses=MFAEnrolResponseSerializer,
        tags=["auth"],
    )
    def post(self, request):
        device, _ = TOTPDevice.objects.get_or_create(
            user=request.user, name="default", defaults={"confirmed": False}
        )

        return Response(
            {
                "provisioning_uri": device.config_url,
                "qr_svg": _qr_svg(device.config_url),
                # Authenticator apps accept a typed secret when a camera is not
                # available — a desktop password manager, or a locked-down phone.
                "secret": _secret_from_uri(device.config_url),
                "already_confirmed": device.confirmed,
            }
        )


def _qr_svg(data: str) -> str:
    """Inline SVG QR code. SVG rather than PNG so it stays crisp and small."""
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode("utf-8")


def _secret_from_uri(uri: str) -> str:
    """Pull the base32 secret out of an otpauth:// URI for manual entry."""
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(uri).query).get("secret", [""])[0]


class LogoutView(APIView):
    """Blacklist the refresh token. Access tokens expire on their own in 15m."""

    @extend_schema(
        summary="Log out",
        request=LogoutRequestSerializer,
        responses={205: OpenApiResponse(description="Refresh token blacklisted")},
        tags=["auth"],
    )
    def post(self, request):
        token = request.data.get("refresh")
        if not token:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(token).blacklist()
        except TokenError:
            pass  # already expired or blacklisted — logout is idempotent
        return Response(status=status.HTTP_205_RESET_CONTENT)


class PasswordChangeView(APIView):
    """Change password and revoke every outstanding session (Doc 12 §13)."""

    @extend_schema(
        summary="Change password",
        request=PasswordChangeSerializer,
        responses={204: OpenApiResponse(description="Changed; all sessions revoked")},
        tags=["auth"],
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.save(update_fields=["password"])

        for outstanding in user.outstandingtoken_set.all():
            try:
                RefreshToken(outstanding.token).blacklist()
            except TokenError:
                continue

        return Response(status=status.HTTP_204_NO_CONTENT)


class HealthView(APIView):
    """Unauthenticated liveness probe for the ALB."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        summary="Liveness probe",
        responses=HealthSerializer,
        auth=[],
        tags=["ops"],
    )
    def get(self, request):
        return Response({"status": "ok"})
