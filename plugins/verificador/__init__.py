"""Plugin de verificación de código.

Cuatro niveles: sintaxis (ast), calidad (ruff+mypy), secretos
hardcodeados y marcadores de conflicto git. El hook post-escritura
ejecuta los cuatro tras cada `crear_archivo`/`escribir_archivo` y
anexa el informe al ToolResult si hay hallazgos.

El toggle de la sidebar controla el hook automático. La tool
`verificar_codigo` está siempre disponible para que el modelo la
invoque bajo demanda.
"""
from .client import (
    ConflictIssue,
    QualityIssue,
    SecretIssue,
    SyntaxIssue,
    check_conflicts,
    check_quality,
    check_syntax,
    scan_secrets,
    verify_all,
)
from .provider import VerificadorProvider

__all__ = [
    "ConflictIssue",
    "QualityIssue",
    "SecretIssue",
    "SyntaxIssue",
    "VerificadorProvider",
    "check_conflicts",
    "check_quality",
    "check_syntax",
    "scan_secrets",
    "verify_all",
]