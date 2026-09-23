"""Plugin de verificación de código.

Verifica la sintaxis de archivos del workspace. Soporta Python vía
`ast.parse`. Otros lenguajes devuelven OK silenciosamente (filosofía
pi-fence-check: solo se reportan errores, el código limpio no hace
ruido).

El plugin expone `verificar_sintaxis(archivo)` como tool siempre
visible al modelo. El toggle de la sidebar controla además un hook
automático que verifica tras cada `crear_archivo`/`escribir_archivo`.
"""
from .client import SyntaxIssue, check_syntax
from .provider import VerificadorProvider

__all__ = ["SyntaxIssue", "check_syntax", "VerificadorProvider"]
