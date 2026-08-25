"""
Print the current TOTP code for a development account.

🔴 Dev only, and guarded the same way seed_dev_users is. This bypasses the
second factor for anyone with shell access — which is fine on a laptop and
unacceptable anywhere else. The point of MFA is that possession of the
authenticator is separate from knowledge of the password; a command that
prints codes collapses that separation.

Use it to exercise the MFA path without installing an authenticator app.
Enrol a real authenticator for anything beyond local development.
"""

from __future__ import annotations

import os
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Print the current TOTP code for a dev account. Dev only."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Account to print a code for")
        parser.add_argument(
            "--create",
            action="store_true",
            help="Create and confirm a TOTP device if the account has none",
        )

    def handle(self, *args, **options):
        if not (settings.DEBUG or os.environ.get("SEED_DEV_USERS_ALLOWED") == "1"):
            raise CommandError(
                "Refusing to run: DEBUG is off and SEED_DEV_USERS_ALLOWED is not set.\n"
                "Printing TOTP codes defeats the second factor entirely."
            )

        email = options["email"]
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(
                f"No account with email {email}. Run 'manage.py seed_dev_users' first."
            ) from None

        device = TOTPDevice.objects.filter(user=user).first()

        if device is None:
            if not options["create"]:
                raise CommandError(
                    f"{email} has no authenticator set up.\n"
                    f"Re-run with --create to make one:\n"
                    f"    manage.py totp_code {email} --create"
                )
            device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
            self.stdout.write(self.style.SUCCESS(f"Created a TOTP device for {email}."))

        # Confirm on the spot: an unconfirmed device would be rejected at
        # sign-in, and the operator asking for a code clearly intends to use it.
        if not device.confirmed:
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            self.stdout.write("Marked the existing device confirmed.")

        # TOTPDevice has no "give me the current code" method — it only
        # verifies. Build the generator from the device's own parameters so
        # this stays correct if step, digits or drift are ever changed.
        totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
        totp.time = time.time()
        code = str(totp.token()).zfill(device.digits)

        seconds_left = device.step - (int(time.time()) % device.step)

        self.stdout.write("")
        self.stdout.write(f"  Account:  {email}")
        self.stdout.write(f"  Code:     {self.style.SUCCESS(code)}")
        self.stdout.write(f"  Valid:    {seconds_left}s")
        self.stdout.write("")
        if seconds_left < 5:
            self.stdout.write("  That code is about to roll over. Re-run for a fresh one.")
            self.stdout.write("")
