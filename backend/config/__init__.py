"""Project configuration package.

Importing the Celery application here guarantees that the shared ``app``
instance is configured as soon as Django starts, so that ``@shared_task``
decorators bind to the right broker.
"""

from .celery import app as celery_app

__all__ = ("celery_app",)
