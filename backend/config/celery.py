"""
Celery application.

🔴 Doc 04 §2: exactly one beat instance. Two beat schedulers means every
scheduled job — every collector, every decay pass, every retention job — runs
twice.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("agricrm")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
