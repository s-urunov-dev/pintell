"""Django settings for the Pintell backend.

Every environment-specific or secret value is read from the environment (or a
local ``.env`` file); nothing sensitive is hardcoded here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import dj_database_url
from celery.schedules import crontab
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent

# Load .env from the backend folder first, then from the repository root, so a
# single root-level .env can drive both docker-compose and a bare-metal run.
load_dotenv(BASE_DIR / ".env")
load_dotenv(REPO_ROOT / ".env")


# --------------------------------------------------------------------------
# Small env helpers
# --------------------------------------------------------------------------
def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------
DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        # Never used outside local development: production start-up fails loudly.
        SECRET_KEY = "django-insecure-local-development-only-key"
    else:
        raise RuntimeError(
            "DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false. "
            "Copy .env.example to .env and fill it in."
        )

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Registers the Postgres-only lookups. No models and no migrations of its
    # own — it is here so `SearchVector`/`SearchQuery` work, which is the
    # full-text fallback under the semantic search (apps/rag_indexer).
    "django.contrib.postgres",
    # Third party
    "rest_framework",
    "django_filters",
    "corsheaders",
    # Local
    "apps.core",
    "apps.tenders",
    "apps.adminpanel",
    # Requirement extraction and the vendor-matching decision engine. The
    # evaluator itself (apps/compliance/expressions.py) has no Django
    # dependency — it is plain Python over dataclasses, so a verdict can be
    # reproduced outside the application entirely.
    "apps.compliance",
    # The expert directory: who a vendor can put forward when a tender names
    # the specialists it needs. Curated by hand, so it is kept out of the
    # compliance app — nothing in it carries a quote, and nothing in it may
    # reach a verdict.
    "apps.experts",
    # Semantic retrieval over the mirror: chunks with page coordinates, a
    # Qdrant collection, and a search endpoint under its own /api/v1/ prefix.
    # Kept out of both `tenders` and `compliance` because it is a **cache** —
    # every vector in it is rebuildable from the mirror, nothing in it reaches
    # a verdict, and a dead Qdrant container must be able to degrade a search
    # without touching either of the two apps that are the product.
    "apps.rag_indexer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
DATABASE_URL = env("DATABASE_URL")
if not DATABASE_URL:
    DATABASE_URL = (
        "postgres://{user}:{password}@{host}:{port}/{name}".format(
            user=env("POSTGRES_USER", "tenders"),
            password=env("POSTGRES_PASSWORD", "tenders"),
            host=env("POSTGRES_HOST", "localhost"),
            port=env("POSTGRES_PORT", "5432"),
            name=env("POSTGRES_DB", "tenders"),
        )
    )

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --------------------------------------------------------------------------
# Passwords / i18n / static
# --------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# --------------------------------------------------------------------------
# Django REST Framework — read-only public API
# --------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        # Undated historical notices must not float to the top of "newest first".
        "apps.core.filters.NullsLastOrderingFilter",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        # Only applies to views that declare a throttle_scope (the console).
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": env("API_THROTTLE_ANON", "120/min"),
        # Keeps the operator login from becoming a password-guessing oracle.
        "admin_login": env("ADMIN_LOGIN_THROTTLE", "10/min"),
        # Job triggers are cheap to click and expensive to run.
        "admin_action": env("ADMIN_ACTION_THROTTLE", "30/min"),
        # Vendor registration and sign-in. Looser than the console's, because
        # this one is used by people who mistype their own password rather
        # than by four operators — but bounded, since it is the endpoint that
        # would otherwise answer "does this company have an account".
        "vendor_auth": env("VENDOR_AUTH_THROTTLE", "20/min"),
        # Semantic search. Its own scope rather than the `anon` rate, because
        # every call here is a metered embedding request while every other
        # anonymous call is a read of the mirror. Sized for a person typing
        # into a search box, not for a crawler walking one.
        "rag_search": env("RAG_SEARCH_THROTTLE", "30/min"),
    },
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ]
    if DEBUG
    else ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}


# --------------------------------------------------------------------------
# CORS — only the frontend origins may call this API from a browser
# --------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:5173,http://127.0.0.1:5173",
)
CORS_ALLOW_ALL_ORIGINS = False
# The operator console authenticates with a session cookie, so credentialed
# requests must be allowed — for the explicitly listed origins only.
CORS_ALLOW_CREDENTIALS = True
# POST is needed by the console's login and job triggers; the public tender API
# remains read-only regardless (its viewsets expose GET only).
CORS_ALLOW_METHODS = ["GET", "HEAD", "OPTIONS", "POST"]


# --------------------------------------------------------------------------
# Cache (used for the /facets endpoint)
# --------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "TIMEOUT": 300,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pintell",
            "TIMEOUT": 300,
        }
    }

# A test run gets its own cache, always — never the deployment's Redis.
#
# The cache is not only the /facets cache. It holds the distributed locks that
# stop two backfill or sync slices running at once, and Django gives the test
# database a separate name but leaves the cache shared. So on any host where
# Celery Beat is live, `manage.py test` competes with the real scheduled job for
# `tenders:backfill:slice-lock`: the job takes it, `run_backfill_slice` correctly
# reports `idle=True`, and a test asserting that a partition finished fails —
# intermittently, depending on where in the five-minute schedule the run landed.
#
# That was observed, not predicted: the suite passed on a laptop whose beat
# container was stopped and failed on the server with beat running, one test
# apart, on identical code.
#
# The deployed image is what gets tested, rather than the working
# tree, which makes this the environment where the suite matters most and the
# one where it was least trustworthy. LocMemCache is per-process, so the lock a
# test takes is its own.
if "test" in sys.argv:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "pintell-tests",
            "TIMEOUT": 300,
        }
    }


# --------------------------------------------------------------------------
# Celery
# --------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", REDIS_URL or "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "")
CELERY_TASK_TIME_LIMIT = 60 * 30
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 25
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = TIME_ZONE

SYNC_INTERVAL_MINUTES = max(1, env_int("SYNC_INTERVAL_MINUTES", 30))
BACKFILL_INTERVAL_MINUTES = max(1, env_int("BACKFILL_INTERVAL_MINUTES", 5))
BACKFILL_ENABLED = env_bool("BACKFILL_ENABLED", True)

CELERY_BEAT_SCHEDULE = {
    "sync-procurement-notices": {
        "task": "apps.tenders.tasks.sync_procurement_notices",
        # Default: every 30 minutes (*/30). Any divisor of 60 keeps a clean grid.
        "schedule": crontab(minute=f"*/{SYNC_INTERVAL_MINUTES}")
        if SYNC_INTERVAL_MINUTES < 60
        else crontab(minute=0, hour=f"*/{SYNC_INTERVAL_MINUTES // 60}"),
        "options": {"expires": 60 * SYNC_INTERVAL_MINUTES},
    },
}

ENRICHMENT_INTERVAL_MINUTES = max(1, env_int("ENRICHMENT_INTERVAL_MINUTES", 15))
ENRICHMENT_ENABLED = env_bool("ENRICHMENT_ENABLED", True)

if ENRICHMENT_ENABLED:
    # Keeps the focus feed enriched: categories, project documents/ESRS, and
    # parsed contract awards. Each task is bounded and idempotent.
    CELERY_BEAT_SCHEDULE["enrich-focus-notices"] = {
        "task": "apps.tenders.tasks.enrich_focus_notices",
        "schedule": crontab(minute=f"*/{ENRICHMENT_INTERVAL_MINUTES}")
        if ENRICHMENT_INTERVAL_MINUTES < 60
        else crontab(minute=0, hour=f"*/{ENRICHMENT_INTERVAL_MINUTES // 60}"),
        "options": {"expires": 60 * ENRICHMENT_INTERVAL_MINUTES},
    }

if BACKFILL_ENABLED:
    # Walks the historical archive one bounded slice at a time. The task is a
    # no-op once every partition is complete, so it can stay scheduled.
    CELERY_BEAT_SCHEDULE["backfill-tender-archive"] = {
        "task": "apps.tenders.tasks.backfill_tender_archive",
        "schedule": crontab(minute=f"*/{BACKFILL_INTERVAL_MINUTES}")
        if BACKFILL_INTERVAL_MINUTES < 60
        else crontab(minute=0, hour=f"*/{BACKFILL_INTERVAL_MINUTES // 60}"),
        "options": {"expires": 60 * BACKFILL_INTERVAL_MINUTES},
    }


# --------------------------------------------------------------------------
# World Bank data source
# --------------------------------------------------------------------------
WORLDBANK = {
    "API_URL": env("WORLDBANK_API_URL", "https://search.worldbank.org/api/v2/procnotices"),
    # Project metadata (financing, agency, status) and the documents archive
    # (every published PDF, including the ESRS) — both keyed by project id.
    "PROJECTS_API_URL": env(
        "WORLDBANK_PROJECTS_API_URL", "https://search.worldbank.org/api/v2/projects"
    ),
    "DOCUMENTS_API_URL": env(
        "WORLDBANK_DOCUMENTS_API_URL", "https://search.worldbank.org/api/v3/wds"
    ),
    "ROWS_PER_PAGE": min(max(env_int("SYNC_ROWS_PER_PAGE", 100), 1), 500),
    "MAX_PAGES": max(env_int("SYNC_MAX_PAGES", 25), 1),
    # Pages per country when the periodic sync runs filtered (INGEST_FOCUS_ONLY).
    # Small on purpose: each country's feed is newest first, so two pages cover
    # a fortnight even for the busiest of them, and anything older is the
    # backfill's problem rather than a job that runs every half hour.
    "FOCUS_PAGES_PER_COUNTRY": max(env_int("SYNC_FOCUS_PAGES_PER_COUNTRY", 2), 1),
    "HTTP_TIMEOUT": env_int("SYNC_HTTP_TIMEOUT", 30),
    # Upstream (Azure Search) rejects os > 100000 with "$skip out of range",
    # which is why the archive is pulled in per-country partitions.
    "MAX_OFFSET": env_int("SYNC_MAX_OFFSET", 100_000),
    # Pages fetched per backfill slice, and the pause between upstream pages.
    # The archive walk issues thousands of requests, so it stays deliberately
    # gentle on a public open-data service.
    "BACKFILL_PAGES_PER_RUN": max(env_int("BACKFILL_PAGES_PER_RUN", 40), 1),
    "BACKFILL_PAGE_DELAY": float(env("BACKFILL_PAGE_DELAY", "0.25") or 0.25),
    "BACKFILL_INTERVAL_MINUTES": max(env_int("BACKFILL_INTERVAL_MINUTES", 5), 1),
    "USER_AGENT": env(
        "SYNC_USER_AGENT",
        "GlobalTenderAggregator/1.0 (+https://example.org; open-data client)",
    ),
    # Public, human-readable notice page on projects.worldbank.org.
    "NOTICE_DETAIL_URL": "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}",
    "ATTRIBUTION": "World Bank Group Procurement Notices (CC BY 4.0)",
}

INITIAL_SYNC_ENABLED = env_bool("INITIAL_SYNC_ENABLED", True)

# --------------------------------------------------------------------------
# Project mirror (financing, documents, ESRS)
# --------------------------------------------------------------------------
# A mirrored project is not a finished thing: its status changes, documents are
# added for years, and the ESRS is often published long after the first notice.
# So a profile is re-fetched once it is `REFRESH_DAYS` old, and one whose last
# attempt failed is retried on a widening delay rather than written off forever.
PROJECTS = {
    "REFRESH_DAYS": max(env_int("PROJECT_REFRESH_DAYS", 14), 1),
    # Projects mirrored per enrichment cycle. Each one costs two upstream
    # requests, so this is what keeps a cycle inside its time limit.
    "BATCH_SIZE": max(env_int("PROJECT_SYNC_BATCH_SIZE", 20), 1),
    # Backoff after a failed attempt: BASE * 2^(failures-1), capped at MAX_DAYS.
    "RETRY_BASE_MINUTES": max(env_int("PROJECT_RETRY_BASE_MINUTES", 60), 1),
    "RETRY_MAX_DAYS": max(env_int("PROJECT_RETRY_MAX_DAYS", 7), 1),
    # How long an on-demand sync request holds its per-project lock. Also the
    # minimum gap between two on-demand requests for the same project.
    "ONDEMAND_LOCK_SECONDS": max(env_int("PROJECT_ONDEMAND_LOCK_SECONDS", 600), 30),
}

# --------------------------------------------------------------------------
# Document harvesting
# --------------------------------------------------------------------------
# The documents a notice points at (Terms of Reference, bidding documents) are
# hosted by the borrower, not by the Bank, and they disappear when the tender
# closes. Mirroring them is therefore time-critical in a way the rest of the
# pipeline is not: what is not collected today cannot be collected later.
#
# The defaults are deliberately gentle. These requests go to a ministry's file
# server or a shared Drive folder, not to an API with a published quota.
HARVEST = {
    "ENABLED": env_bool("HARVEST_ENABLED", True),
    # Where the raw bytes live, content-addressed by SHA-256. Kept outside the
    # database because these are multi-megabyte binaries that no query reads —
    # the extracted text is what the pipeline actually consumes.
    "DIR": Path(env("HARVEST_DIR", str(REPO_ROOT / "data" / "harvest"))),
    "BATCH_SIZE": max(env_int("HARVEST_BATCH_SIZE", 25), 1),
    "INTERVAL_MINUTES": max(env_int("HARVEST_INTERVAL_MINUTES", 10), 1),
    "HTTP_TIMEOUT": max(env_int("HARVEST_HTTP_TIMEOUT", 60), 5),
    # A hard ceiling on one download. Bidding documents run to tens of
    # megabytes; anything past this is a video or a mis-linked archive.
    "MAX_BYTES": max(env_int("HARVEST_MAX_BYTES", 60 * 1024 * 1024), 1024),
    # Pause between two requests to the same host, in seconds.
    "PER_HOST_DELAY": float(env("HARVEST_PER_HOST_DELAY", "1.5") or 1.5),
    # Extracted text kept per document. Well above a bidding document's
    # qualification section, well below a 500-page appendix of drawings.
    "MAX_TEXT_CHARS": max(env_int("HARVEST_MAX_TEXT_CHARS", 400_000), 1000),
    # Same backoff shape as PROJECTS: BASE * 2^(attempts-1), capped by MAX.
    "RETRY_BASE_MINUTES": max(env_int("HARVEST_RETRY_BASE_MINUTES", 120), 1),
    "RETRY_MAX_DAYS": max(env_int("HARVEST_RETRY_MAX_DAYS", 3), 1),
}

if HARVEST["ENABLED"]:
    # Scheduled separately from the enrichment cycle, and more often, because
    # this is the one job whose window closes: a borrower-hosted TOR stops
    # answering once the tender does. Everything else can wait a cycle.
    _harvest_interval = HARVEST["INTERVAL_MINUTES"]
    CELERY_BEAT_SCHEDULE["harvest-notice-documents"] = {
        "task": "apps.tenders.tasks.harvest_notice_documents",
        "schedule": crontab(minute=f"*/{_harvest_interval}")
        if _harvest_interval < 60
        else crontab(minute=0, hour=f"*/{_harvest_interval // 60}"),
        "options": {"expires": 60 * _harvest_interval},
    }

# --------------------------------------------------------------------------
# Compliance extraction
# --------------------------------------------------------------------------
# The scheduled half of the compliance work: which tenders get read
# automatically, how many per cycle, and whether it runs at all.
#
# The batch size is small because the population is small — around thirty open
# tenders at any moment across the whole focus region — so a cycle that reads
# twenty-five is already reading almost everything there is. It exists to bound
# a bad day (an upstream burst, a re-extraction), not to ration normal work.
COMPLIANCE = {
    "AUTO_EXTRACT": env_bool("COMPLIANCE_AUTO_EXTRACT", True),
    "AUTO_BATCH_SIZE": max(env_int("COMPLIANCE_AUTO_BATCH_SIZE", 25), 1),
}

if COMPLIANCE["AUTO_EXTRACT"]:
    # A safety net rather than the trigger: the sync calls the task directly
    # when it finds new notices, so this entry usually finds nothing to do.
    # It matters after a worker restart, a failed sync, or a deploy that
    # happened between cycles — the cases where "it will run next sync" is a
    # guess rather than a fact.
    CELERY_BEAT_SCHEDULE["extract-active-requirements"] = {
        "task": "apps.compliance.tasks.extract_active_requirements",
        "schedule": crontab(minute="15,45"),
        "options": {"expires": 60 * 25},
    }


# --------------------------------------------------------------------------
# Focus filters (stage-1 product scope)
# --------------------------------------------------------------------------
# Which country group and notice types the actionable feed aggregates. Both
# are deliberately configuration rather than code so the scope can widen to
# other regions and other IFIs without a release.
FOCUS_COUNTRY_GROUP = env("FOCUS_COUNTRY_GROUP", "cis_plus")
FOCUS_OPEN_ONLY = env_bool("FOCUS_OPEN_ONLY", True)

# Whether the focus group also bounds what is *stored*, not just what the feed
# shows. On, the ingest drops a notice from any other country before it is
# written; off, the mirror keeps the whole World Bank archive and the group is
# only a display filter.
#
# It has to be enforced at write time rather than by pruning afterwards: the
# incremental sync and the backfill's `recent` partition both walk the
# unfiltered upstream feed, so anything deleted would simply come back on the
# next cycle. Turning this off widens the mirror again — it does not restore
# what was already dropped.
INGEST_FOCUS_ONLY = env_bool("INGEST_FOCUS_ONLY", True)


# --------------------------------------------------------------------------
# AI enrichment (Claude) — optional
# --------------------------------------------------------------------------
# Without an API key every AI feature degrades gracefully: classification
# falls back to keyword rules, website discovery is skipped.
#
# **One key, two unrelated consumers, and they are gated separately on
# purpose.** Compliance extraction is the product; classification is a nicety
# that predates it. Before this split, setting `ANTHROPIC_API_KEY` woke both at
# once — and the classifier has a standing Celery schedule over the whole
# mirror, so the first act of enabling compliance would have been to send
# 25,000 notices to Opus unattended. Enabling a paid path must be a decision,
# never a side effect of enabling a different one.
ANTHROPIC = {
    "ENABLED": env_bool("AI_ENABLED", True),
    "API_KEY": env("ANTHROPIC_API_KEY"),
    "MODEL": env("AI_MODEL", "claude-opus-5"),
    # The chat's model, when it should differ from extraction's. Empty means
    # "whatever `MODEL` is", so a deployment that never sets it is unchanged.
    #
    # It exists because the two jobs pull opposite ways. Extraction is offline,
    # runs over thousands of English notices and is graded against a gold set,
    # so the cheapest tier that passes is the right one. The chat is one call
    # per question, read by a vendor, and has to write Uzbek or Russian from
    # English sources — a quality nothing in the pipeline measures. Tying them
    # to one variable meant the cheap extraction setting silently chose how the
    # product speaks.
    "CHAT_MODEL": env("AI_CHAT_MODEL", ""),
    # How hard the chat's model works per answer. Measured on the deployed
    # archive: at the default (`high`, with adaptive thinking on) a general
    # question took **45 seconds** to answer — correct, well cited, and far too
    # slow for a person waiting with a cursor blinking. The work here is not
    # reasoning: the passages are already selected and the schema already
    # fixes the shape, so what is left is reading sixteen paragraphs and
    # writing what they say. `low` is sized for that, and the trade is visible
    # in the one number that matters — `unsupported`, which stays measured
    # either way.
    "CHAT_EFFORT": env("AI_CHAT_EFFORT", "low"),
    # The cheap tier, for questions that are a lookup rather than a reading
    # (D60). **Empty by default, and that is the decision, not an omission.**
    # Routing is live the moment this is set, and what it would cost is
    # already measured: the deployed server ran the whole chat on Haiku and
    # the Uzbek came back ungrammatical — "zarorat", "savdo-sotuvchi
    # shartlari". So the fast tier is opt-in, and the recommended value is
    # `claude-sonnet-5` rather than the cheapest thing that answers.
    "CHAT_MODEL_FAST": env("AI_CHAT_MODEL_FAST", ""),
    # The deep tier, for a question that asks the model to weigh several
    # passages against each other. Falls through to `CHAT_MODEL`/`MODEL`.
    "CHAT_MODEL_DEEP": env("AI_CHAT_MODEL_DEEP", ""),
    # Effort per tier. A lookup does not need the reasoning budget a
    # comparison does, and effort is the cheaper of the two levers.
    "CHAT_EFFORT_FAST": env("AI_CHAT_EFFORT_FAST", "low"),
    "CHAT_EFFORT_DEEP": env("AI_CHAT_EFFORT_DEEP", "medium"),
    "TIMEOUT": env_int("AI_TIMEOUT", 120),
    "MAX_RETRIES": env_int("AI_MAX_RETRIES", 2),
    # Off unless asked for, and the default stays off even with a key present:
    # 25,248 focus notices carry no category, the beat job classifies 40 every
    # 15 minutes, and every one whose keyword confidence falls under the
    # threshold becomes an Opus call. Nobody would have chosen that; it would
    # simply have started.
    "CLASSIFY_ENABLED": env_bool("AI_CLASSIFY_ENABLED", False),
    # Classification: cheap effort, tight output — the schema does the work.
    "CLASSIFY_EFFORT": env("AI_CLASSIFY_EFFORT", "low"),
    "CLASSIFY_MAX_TOKENS": env_int("AI_CLASSIFY_MAX_TOKENS", 2000),
    "CLASSIFY_MAX_CHARS": env_int("AI_CLASSIFY_MAX_CHARS", 6000),
    # Rule confidence at or above this skips the model entirely.
    "CLASSIFY_RULE_THRESHOLD": float(env("AI_CLASSIFY_RULE_THRESHOLD", "0.55") or 0.55),
    "CLASSIFY_BATCH_SIZE": env_int("AI_CLASSIFY_BATCH_SIZE", 40),
    # Website discovery runs live web searches — metered on purpose.
    "ENRICH_EFFORT": env("AI_ENRICH_EFFORT", "low"),
    "ENRICH_MAX_TOKENS": env_int("AI_ENRICH_MAX_TOKENS", 1500),
    "ENRICH_MAX_SEARCHES": env_int("AI_ENRICH_MAX_SEARCHES", 3),
    "ENRICH_BATCH_SIZE": env_int("AI_ENRICH_BATCH_SIZE", 10),
}


# --------------------------------------------------------------------------
# Web-search enrichment provider — optional, and free to run
# --------------------------------------------------------------------------
# The people/website lookups need a model that can search the web. Two
# providers can do it and the code treats them as interchangeable (see
# ``apps/tenders/services/ai/providers.py``): Claude's server-side web search,
# and Gemini with Google Search grounding. Gemini is here because its free
# tier bundles the search, so this feature costs nothing to run.
#
# "auto" picks whichever key is present, preferring Gemini precisely because
# it is the free one — set AI_PROVIDER explicitly to override.
GEMINI = {
    "API_KEY": env("GEMINI_API_KEY"),
    "MODEL": env("GEMINI_MODEL", "gemini-2.5-flash"),
    "TIMEOUT": env_int("GEMINI_TIMEOUT", 120),
}

AI_PROVIDER = env("AI_PROVIDER", "auto").strip().lower()


# --------------------------------------------------------------------------
# Semantic index — Qdrant, and the embeddings that fill it
# --------------------------------------------------------------------------
# A third consumer of an API key, gated separately from the other two for the
# reason written above ANTHROPIC: a key is permission to spend, never an
# instruction to. `RAG_ENABLED` is off by default, so pulling this release does
# not start embedding thirty thousand notices on somebody's free tier.
#
# **What is embedded, and what is not.** The passages are World Bank notice
# bodies and the borrower documents those notices link to — published material
# the public API already serves. Vendor profile data is a separate question
# with a legal answer attached (D43: it does not leave the deployment)
# and is not sent here. `EmbeddingService` is the seam where a local model
# replaces the hosted one if that ever changes.
#
# **VECTOR_SIZE is the collection's width and the request's width at once.**
# One setting on purpose: in Qdrant the width is fixed when the collection is
# created, so a model whose output does not match it does not fail loudly — it
# fails at upsert, or worse, after a re-create nobody meant to do. Changing
# either the model or the width invalidates what is already embedded; see
# `IndexedSource.stale`, which treats both as staleness rather than pretending
# vectors from two models are comparable.
RAG = {
    "ENABLED": env_bool("RAG_ENABLED", False),
    # Local container by default. There is no hosted fallback and no cloud
    # URL baked in: the index is rebuildable from the mirror, so the reason to
    # run it locally (no per-call cost, no notice text crossing a network that
    # is not ours) holds at every scale this product has.
    "QDRANT_URL": env("QDRANT_URL", "http://qdrant:6333"),
    "QDRANT_API_KEY": env("QDRANT_API_KEY"),
    "QDRANT_TIMEOUT": env_int("QDRANT_TIMEOUT", 30),
    "COLLECTION": env("RAG_COLLECTION", "notice_chunks"),
    "VECTOR_SIZE": env_int("RAG_VECTOR_SIZE", 768),
    # Gemini's embedding model, at `output_dimensionality=768` so the vector
    # width above is what comes back.
    #
    # **Not `text-embedding-004`.** That was the first default here and it is
    # retired: the API answers `404 ... is not found for API version v1beta`,
    # which would have failed the archive run on its first request rather than
    # degrading. Verified against the live endpoint before the first import,
    # which is the only way this kind of fact is worth having.
    "EMBED_MODEL": env("RAG_EMBED_MODEL", "gemini-embedding-001"),
    # Falls back to the enrichment key, so a deployment that already has one
    # does not need a second. Separate name so it *can* be separated — the two
    # consumers have very different call volumes and quota is per key.
    "EMBED_API_KEY": env("RAG_EMBED_API_KEY") or env("GEMINI_API_KEY"),
    "EMBED_TIMEOUT": env_int("RAG_EMBED_TIMEOUT", 120),
    # Texts per embedding request. The provider caps a batch and the cap is
    # below the chunk count of one long bidding document, so this is a real
    # bound on throughput rather than a formality.
    "EMBED_BATCH": max(env_int("RAG_EMBED_BATCH", 100), 1),
    "EMBED_MAX_CHARS": max(env_int("RAG_EMBED_MAX_CHARS", 8000), 200),
    "EMBED_MAX_RETRIES": max(env_int("RAG_EMBED_MAX_RETRIES", 5), 0),
    # **Texts** per minute the embedder will not exceed; 0 is unpaced.
    #
    # Texts rather than HTTP calls because that is what the provider counts:
    # its batch endpoint calls each text in the batch a "request", so a single
    # call carrying 100 texts spends a whole free-tier minute. Verified against
    # the live endpoint — 100 texts in one call returns 200, ten more a second
    # later returns 429.
    #
    # Free tier is 100/min, which puts a 112,000-chunk archive at about
    # nineteen hours. Enabling billing is what changes that, not batching.
    "EMBED_TEXTS_PER_MINUTE": max(env_int("RAG_EMBED_TEXTS_PER_MINUTE", 0), 0),
    # Embedding requests in flight at once. 1 is sequential.
    #
    # Raise it only together with lifting the pace above: while a per-minute
    # budget binds, threads simply queue behind it. Once billing lifts the
    # ceiling, latency binds instead — a sequential stream indexes ~75 chunks a
    # minute however much the plan allows, which is a day for this archive.
    "EMBED_CONCURRENCY": max(env_int("RAG_EMBED_CONCURRENCY", 1), 1),
    "EMBED_BACKOFF_BASE": float(env("RAG_EMBED_BACKOFF_BASE", "1.5") or 1.5),
    # Points per upsert. Larger batches are faster per point and hold more of
    # the run in memory; 256 chunks of ~1 kB is a quarter of a megabyte in
    # flight, which is the right end of that trade for a worker container.
    "UPSERT_BATCH": max(env_int("RAG_UPSERT_BATCH", 256), 1),
    # Chunking. A target rather than a maximum — see `ExtractionService`, which
    # never splits a sentence or a page to hit it.
    "CHUNK_CHARS": max(env_int("RAG_CHUNK_CHARS", 900), 100),
    "MIN_CHUNK_CHARS": max(env_int("RAG_MIN_CHUNK_CHARS", 80), 1),
    # Sentences repeated between neighbouring chunks. Off: the cut is already
    # on a sentence boundary, so the usual argument for overlap does not apply,
    # and overlapping ranges mean one passage returned twice.
    "CHUNK_OVERLAP": max(env_int("RAG_CHUNK_OVERLAP", 0), 0),
    "SEARCH_LIMIT": max(env_int("RAG_SEARCH_LIMIT", 5), 1),
    # Passages the chat shows the model. Separate from `SEARCH_LIMIT` because
    # the two answer different questions: the search box lists results a person
    # scrolls, the chat fills a context window a model reads.
    #
    # Sixteen rather than eight. At eight, a general question ("what turnover
    # do IT tenders require?") was answered from three or four distinct
    # notices once near-duplicate template paragraphs were counted — too thin a
    # base for the range the prompt asks for, which is where "ma'lumot yetarli
    # emas" came from. The score floor was not the binding constraint: measured
    # on the deployed archive, all 200 nearest neighbours of every test
    # question scored above it.
    "CHAT_PASSAGES": max(env_int("RAG_CHAT_PASSAGES", 16), 1),
    "SEARCH_MAX_LIMIT": max(env_int("RAG_SEARCH_MAX_LIMIT", 50), 1),
    # Cosine always returns *something*. Without a floor, a question the
    # corpus cannot answer comes back with five confident-looking passages
    # under a citation badge that makes them look sourced.
    "SCORE_THRESHOLD": float(env("RAG_SCORE_THRESHOLD", "0.5") or 0.5),
    # Keeping the index current, unattended. Off unless `RAG_ENABLED` is on,
    # and bounded per run — see `apps.rag_indexer.tasks` for what it will and
    # will not embed without a human watching.
    "AUTO_INDEX": env_bool("RAG_AUTO_INDEX", True),
    "AUTO_INDEX_INTERVAL_MINUTES": max(env_int("RAG_AUTO_INDEX_INTERVAL_MINUTES", 20), 1),
    # Sources per run. A synced feed adds a few dozen notices a day, so this is
    # a ceiling on the surprise rather than a throughput target: a run that
    # wants more than this leaves the rest for the next one.
    "AUTO_INDEX_LIMIT": max(env_int("RAG_AUTO_INDEX_LIMIT", 60), 1),
    # ----------------------------------------------------------------------
    # Answering the same question twice — the semantic cache (D57)
    # ----------------------------------------------------------------------
    # Its own collection, never the archive's: a stored answer is not a
    # passage, and a cache entry appearing in a citation list is the one
    # failure this product cannot have. Dropping the collection costs the hit
    # rate and nothing else, which is the same trade `COLLECTION` already makes.
    "CACHE_ENABLED": env_bool("RAG_CACHE_ENABLED", True),
    "CACHE_COLLECTION": env("RAG_CACHE_COLLECTION", "chat_cache"),
    # Cosine at or above this returns the stored answer. 0.92 is the brief's
    # number and it is deliberately high: the two questions have to *mean* the
    # same thing, because the reader is shown the earlier answer verbatim.
    "CACHE_THRESHOLD": float(env("RAG_CACHE_THRESHOLD", "0.92") or 0.92),
    # How long an answer may be served. The archive moves under it — a tender
    # closes, a notice is synced — so a cached answer is a claim about a corpus
    # that no longer exists once this elapses. Six hours is under the sync
    # cadence a deadline can cross unnoticed.
    "CACHE_TTL_SECONDS": max(env_int("RAG_CACHE_TTL_SECONDS", 21600), 60),
    # Entries kept. Beyond this the oldest are pruned on write: this table is
    # filled by an anonymous public endpoint and "keep everything" is not a
    # decision anyone makes deliberately.
    "CACHE_MAX_ENTRIES": max(env_int("RAG_CACHE_MAX_ENTRIES", 5000), 1),
    # ----------------------------------------------------------------------
    # Hybrid retrieval — dense + lexical, fused by rank (D58)
    # ----------------------------------------------------------------------
    # On by default because the failure it fixes is total: a tender reference
    # like `TRIP-CS-01` has no semantic neighbourhood, so the dense arm returns
    # documents *about* consulting selection and never the notice the reader
    # named. Fusion is over ranks, not scores — see `services/fusion.py` for
    # why that does not contradict the search module's refusal to merge.
    "HYBRID": env_bool("RAG_HYBRID", True),
    # Reciprocal Rank Fusion's smoothing constant. 60 is the value the original
    # paper measured and it is left alone deliberately: tuning it is a claim
    # about this corpus that no gold set has been run to support yet.
    "RRF_K": max(env_int("RAG_RRF_K", 60), 1),
    # Candidates each arm contributes before fusion.
    "HYBRID_CANDIDATES": max(env_int("RAG_HYBRID_CANDIDATES", 20), 1),
    # Rows the lexical arm computes a relevance rank over.
    #
    # Measured on the 25,463-notice development mirror, and it is the single
    # number that decides whether hybrid retrieval is affordable. `ts_rank`
    # recomputes `to_tsvector` per row — the GIN index finds the matches, it
    # does not store a vector to rank with — so the cost is linear in the
    # *match count*, not in the result count: "annual turnover requirement"
    # matches 697 notices and ranking all of them costs 570 ms. Sampling and
    # then ranking: 168 ms at 200, 250 ms at 300, 374 ms at 400.
    #
    # What is traded is a guarantee, and it is worth stating plainly: above
    # this many matches, the best-ranked notice is no longer certain to be in
    # the sample. The dense arm is unaffected and fusion still has both.
    # Production, on longer bodies than the mirror above, measured 652 ms at
    # this cap — and the cap was hiding that "consulting services" matches
    # 6,744 notices, so 4% were ranked and the sample was index order (D63).
    "LEXICAL_RANK_CAP": max(env_int("RAG_LEXICAL_RANK_CAP", 300), 1),
    # Rank against the stored `search_vector` column instead of recomputing
    # `to_tsvector` per matching row (tenders migrations 0023-0025).
    #
    # **Off by default, and it is a cutover rather than a preference.** The
    # column is added empty and filled by `manage.py backfill_search_vector`;
    # until that reports 0 remaining, a search reading the column returns
    # *fewer notices*, not slower ones — the one failure shape worth a flag to
    # avoid, because it looks like a working search. Deploy, backfill, then
    # switch this on. `--status` prints the sentence that says when.
    #
    # What it buys, measured on a staging copy of production (D63): the same
    # 300-row ranking at 68 ms rather than 652, or all 6,744 matches at 354 ms
    # warm — faster than today's capped query, and ranking all of them.
    #
    # **The cap stays either way.** Uncapped is not free even from the stored
    # column: "procurement" matches 22,341 notices, 88% of the mirror, and
    # ranking all of them still costs 2.6 s. The cap stops being the difference
    # between 4% and 100% and becomes a bound on the pathological query, which
    # is what it should have been.
    "LEXICAL_STORED_VECTOR": env_bool("RAG_LEXICAL_STORED_VECTOR", False),
    # ----------------------------------------------------------------------
    # Reranking — a second, sharper pass over the candidates (D59)
    # ----------------------------------------------------------------------
    # `none` is the default and the only backend that needs no new dependency
    # or vendor. `cohere` and `local` are implemented and each is one variable
    # away; neither is switched on here, because a rerank model is a decision
    # about cost, a third party and a 2 GB image — see docs/DECISIONS.md.
    "RERANK_BACKEND": env("RAG_RERANK_BACKEND", "none").strip().lower(),
    "RERANK_MODEL": env("RAG_RERANK_MODEL", "bge-reranker-v2-m3"),
    "RERANK_API_KEY": env("RAG_RERANK_API_KEY"),
    "RERANK_TIMEOUT": max(env_int("RAG_RERANK_TIMEOUT", 15), 1),
    # Passages that survive the rerank. The brief's 3-5; five, because a
    # general question is answered from the range across notices and three
    # passages is not a range.
    "RERANK_TOP_N": max(env_int("RAG_RERANK_TOP_N", 5), 1),
    # ----------------------------------------------------------------------
    # What the model is actually sent
    # ----------------------------------------------------------------------
    # Characters of one passage in the prompt. Compression collapses runs of
    # whitespace and strips markup left in a borrower's body; this bounds the
    # one pathological table that would otherwise spend the window alone.
    "PASSAGE_MAX_CHARS": max(env_int("RAG_PASSAGE_MAX_CHARS", 1200), 200),
    # Structural chunking for what a vendor uploads (D61). Applies to intake
    # documents only — the mirrored archive keeps the chunker its offsets were
    # measured with, because re-chunking it would mean re-embedding it.
    "STRUCTURAL_CHUNKING": env_bool("RAG_STRUCTURAL_CHUNKING", True),
    # How often the warm-up task touches the collection, in minutes. 0 turns
    # it off. Five is chosen against the symptom rather than against a
    # benchmark: the pages have to survive between one reader and the next,
    # and on an idle box that is a question about the kernel's reclaim, not
    # about Qdrant. Each run is three local searches and no embedding.
    "WARM_INTERVAL_MINUTES": max(env_int("RAG_WARM_INTERVAL_MINUTES", 5), 0),
}

RAG_AUTO_INDEX_MINUTES = RAG["AUTO_INDEX_INTERVAL_MINUTES"]

if RAG["ENABLED"] and RAG["AUTO_INDEX"]:
    # The semantic index had no scheduled job, so a notice synced after the
    # last hand-run `archive_to_qdrant` was invisible to search, to the chat
    # and to the similar-awards panel until somebody remembered. Found on the
    # deployed server on 2026-08-11: three of the twenty-two open tenders had
    # no points in the collection at all, and their panels were empty for that
    # reason and no other.
    CELERY_BEAT_SCHEDULE["index-new-notices"] = {
        "task": "apps.rag_indexer.tasks.index_new_notices",
        "schedule": crontab(minute=f"*/{RAG_AUTO_INDEX_MINUTES}")
        if RAG_AUTO_INDEX_MINUTES < 60
        else crontab(minute=0, hour=f"*/{RAG_AUTO_INDEX_MINUTES // 60}"),
        "options": {"expires": 60 * RAG_AUTO_INDEX_MINUTES},
    }

if RAG["ENABLED"] and RAG["WARM_INTERVAL_MINUTES"]:
    # Keeps the collection's pages resident. The deployed server's first
    # search after an idle spell took 2.1 s against 32 ms warm (D63) — a
    # mostly-idle box lets the kernel reclaim what Qdrant memory-maps, and the
    # reader who pays for that is whoever opens the product first that day.
    #
    # Costs no embedding: the task searches with fixed vectors, because what a
    # warm-up looks for does not matter and calling a paid provider on a timer
    # forever does.
    CELERY_BEAT_SCHEDULE["warm-index"] = {
        "task": "apps.rag_indexer.tasks.warm_index",
        "schedule": crontab(minute=f"*/{RAG['WARM_INTERVAL_MINUTES']}"),
        "options": {"expires": 60 * RAG["WARM_INTERVAL_MINUTES"]},
    }


# --------------------------------------------------------------------------
# Security hardening (the strict flags only bite when DEBUG is off)
# --------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = env("SESSION_COOKIE_SAMESITE", "Lax")
# Must stay readable by JavaScript: the console reads it to set X-CSRFToken.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = env("CSRF_COOKIE_SAMESITE", "Lax")
# Operator sessions expire rather than lingering on a shared machine.
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 12)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("SESSION_EXPIRE_AT_BROWSER_CLOSE", False)

if not DEBUG:
    # `SECURE_SSL_REDIRECT` and `SECURE_HSTS_SECONDS` default *off*, and
    # `manage.py check --deploy` says so every time. That is the right default
    # rather than an oversight: this deployment is served over plain HTTP on an
    # address with no certificate, and a redirect to a scheme nothing answers
    # takes the site down, while an HSTS header pins that mistake in every
    # visitor's browser for its max-age. Both flip to on through the
    # environment on the day TLS terminates in front of the app, which is the
    # only day they are safe.
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
    CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------
LOG_LEVEL = env("DJANGO_LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
