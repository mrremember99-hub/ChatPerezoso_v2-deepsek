"""Plugin de shell: ejecuta comandos simples sobre el workspace.

Diseñado con criterio conservador: sin shell=True, sin metacaracteres,
sin sudo, timeout duro, entorno mínimo y confirmación siempre.
"""
from .client import ShellClient, ShellError, analyze_risk
from .provider import ShellProvider

__all__ = ["ShellClient", "ShellError", "analyze_risk", "ShellProvider"]
