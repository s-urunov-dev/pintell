"""Uniform API error envelope.

Every error the API emits looks the same, so the frontend has exactly one
shape to handle:

    {"error": {"code": "not_found", "message": "...", "status": 404}}

``message`` is localised to the caller's language (``?lang=`` or
``Accept-Language``, defaulting to Uzbek). ``code`` is the stable identifier:
clients that carry their own catalogue key off that and never see the wording
here, which is why the code is emitted for field errors too.
"""

from __future__ import annotations

import logging
from typing import Any

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

from .i18n import DEFAULT_LANGUAGE, resolve_language, translate

logger = logging.getLogger(__name__)


class LocalizedMixin:
    """Carries a catalogue key so the handler can render it per request.

    An exception is raised long before the response language is known, so the
    message cannot be built at raise time. Subclasses name a ``message_code``
    from :mod:`apps.core.i18n` and any ``{placeholder}`` values instead.
    """

    message_code: str = ""

    def __init__(
        self,
        *args: Any,
        message_code: str = "",
        message_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ):
        if message_code:
            self.message_code = message_code
        self.message_params = message_params or {}
        # A readable English detail keeps logs and the DRF browsable API sane.
        if not args and not kwargs.get("detail"):
            fallback = translate(self.message_code, DEFAULT_LANGUAGE, **self.message_params)
            if fallback:
                args = (fallback,)
        super().__init__(*args, **kwargs)


class LocalizedAPIException(LocalizedMixin, APIException):
    """An ``APIException`` whose message is looked up per response language."""


class LocalizedValidationError(LocalizedMixin, ValidationError):
    """A 400 whose message is looked up per response language."""

    message_code = "invalid"

# Django's Http404/PermissionDenied carry no DRF ``default_code``, so fall back
# to a code derived from the HTTP status.
_STATUS_CODES = {
    400: "invalid",
    401: "not_authenticated",
    403: "permission_denied",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    415: "unsupported_media_type",
    429: "throttled",
    500: "error",
    503: "service_unavailable",
}


def _localize_details(details, language: str):
    """Rewrite serializer errors as ``{field: [{code, message}, …]}``.

    DRF's ``ErrorDetail`` is a ``str`` subclass that also carries ``.code``
    (``required``, ``blank``, ``max_length``, …). Keeping the code lets a
    client localise a field error itself; the message is translated here for
    everyone else, falling back to DRF's own English when the code is one we
    do not catalogue.
    """
    if isinstance(details, dict):
        return {key: _localize_details(value, language) for key, value in details.items()}
    if isinstance(details, (list, tuple)):
        return [_localize_details(item, language) for item in details]

    code = getattr(details, "code", None)
    text = str(details)
    if not code:
        return text
    return {"code": code, "message": translate(f"field.{code}", language) or text}


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled exception: let Django's 500 machinery log and render it.
        logger.exception("Unhandled API exception", exc_info=exc)
        return None

    language = resolve_language((context or {}).get("request"))
    # `ErrorDetail.code` carries the most specific identifier — it is what a
    # permission class's `code` and an explicitly-coded raise land on. Only
    # then fall back to the exception class's default and to the status.
    code = (
        getattr(getattr(exc, "detail", None), "code", None)
        or getattr(exc, "default_code", None)
        or _STATUS_CODES.get(response.status_code, "error")
    )

    detail = response.data
    if isinstance(detail, dict) and "detail" in detail:
        fallback = str(detail["detail"])
        extra = {k: v for k, v in detail.items() if k != "detail"}
    elif isinstance(detail, dict):
        fallback = ""
        extra = detail
    else:
        fallback = ""
        extra = {"detail": detail}

    # An exception that named its own catalogue key wins; otherwise the code's
    # entry does, because DRF's own wording is English-only.
    message = None
    message_code = getattr(exc, "message_code", "")
    if message_code:
        message = translate(message_code, language, **getattr(exc, "message_params", {}))
    if not message:
        message = translate(code, language) or fallback or translate("error", language)

    payload = {
        "error": {
            "code": code,
            "message": message,
            "status": response.status_code,
            "language": language,
        }
    }
    if extra:
        payload["error"]["details"] = _localize_details(extra, language)
    response.data = payload
    return response
