"""Tool schema compilation (TSCG-style). Spec: §5.
Estado: S0 (stub). Implementacion en S3.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompilerProfile:
    """Perfil de operadores TSCG."""

    name: str
    operators: tuple[str, ...]


CONSERVATIVE = CompilerProfile(
    name="conservative",
    operators=("sdm", "tas", "dro"),
)
BALANCED = CompilerProfile(
    name="balanced",
    operators=("sdm", "tas", "dro", "cfl", "cfo", "cas"),
)
AGGRESSIVE = CompilerProfile(
    name="aggressive",
    operators=("sdm", "tas", "dro", "cfl", "cfo", "cas", "sadf", "ccp"),
)


# Siglas de tipo para TAS. Se aplican al nombre del tipo crudo.
_TYPE_ABBREV: dict[str, str] = {
    "string": "s",
    "integer": "i",
    "number": "n",
    "boolean": "b",
    "array": "a",
    "object": "o",
}


def _fmt_type(spec: dict) -> str:
    """Devuelve la sigla del tipo. Soporta enum inline."""
    raw = spec.get("type", "")
    if isinstance(raw, list):
        # Union types: "string|null" -> "s?"
        parts: list[str] = [
            _TYPE_ABBREV.get(t, t)
            for t in raw
            if isinstance(t, str) and t != "null"
        ]
        nullable = "null" in raw
        s = "|".join(parts) if parts else "?"
        return f"{s}?" if nullable else s
    abbrev = _TYPE_ABBREV.get(raw, raw) if raw else "?"
    enum = spec.get("enum")
    if enum:
        # "enum: a|b|c" — valores permitidos inline.
        opts = "|".join(str(v) for v in enum)
        return f"{abbrev} [{opts}]"
    return abbrev


def _fmt_param(
    name: str, spec: dict, *, required: bool,
) -> str:
    """Una linea por parametro: '· name: type * — descripcion'."""
    t = _fmt_type(spec)
    req = " *" if required else ""
    desc = spec.get("description", "")
    # DRO: quitar comillas redundantes y colapsar saltos.
    desc = " ".join(str(desc).split())
    line = f"· {name}: {t}{req}"
    if desc:
        line += f" \u2014 {desc}"
    if "default" in spec:
        line += f" (def: {spec['default']!r})"
    return line


class ToolSchemaCompiler:
    """Compilador de schemas a texto token-efficiente.

    Aplica SDM+TAS+DRO sobre el JSON schema que produce
    ToolRegistry.definitions(). El resultado es texto plano que
    el modelo interpreta sin parsear JSON.
    """

    def __init__(self, profile: CompilerProfile = CONSERVATIVE) -> None:
        self.profile = profile

    def compile(self, tool: dict) -> str:
        """JSON schema de tool -> texto estructurado.

        `tool` es el dict completo `{type: function, function: {...}}`
        o directamente `{name, description, parameters}`.
        """
        fn = tool.get("function", tool)
        name = str(fn.get("name", "?"))
        desc = " ".join(str(fn.get("description", "")).split())

        params = fn.get("parameters", {})
        props = params.get("properties", {}) or {}
        required = set(params.get("required", []) or [])

        lines = [f"## {name}"]
        if desc:
            lines.append(desc)
        if props:
            lines.append("")
            for pname, pspec in props.items():
                if not isinstance(pspec, dict):
                    pspec = {"type": "?"}
                lines.append(
                    _fmt_param(
                        pname, pspec, required=pname in required,
                    ),
                )
        return "\n".join(lines)

    def compile_all(self, tools: list[dict]) -> str:
        """Lista de schemas -> bloque unico separado por linea."""
        return "\n\n".join(self.compile(t) for t in tools)
