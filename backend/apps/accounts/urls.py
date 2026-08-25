"""Auth routes (Doc 11 §2)."""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("me/", views.MeView.as_view(), name="me"),
    path("mfa/enrol/", views.MFAEnrolView.as_view(), name="mfa-enrol"),
    path("mfa/verify/", views.MFAVerifyView.as_view(), name="mfa-verify"),
    path("password/change/", views.PasswordChangeView.as_view(), name="password-change"),
]
