from django.apps import AppConfig


class RagIndexerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rag_indexer"
    verbose_name = "Semantic index (Qdrant)"
