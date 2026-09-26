from pathlib import Path

p = Path("core/tools.py")
src = p.read_text(encoding="utf-8")

# --- 1. Caller: anchor en una sola línea, tolerante a whitespace ---
anchor = "        arguments = _normalise_args(arguments, spec)\n"
assert anchor in src, "línea del caller no encontrada"
insert = (
    "        arguments = _normalise_args(arguments, spec)\n"
    "        if isinstance(arguments, str):\n"
    "            return arguments\n"
)
src = src.replace(anchor, insert, 1)

# --- 2. Función: reemplazo por índices entre marcadores ---
new_fn = '''def _normalise_args(
    arguments: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any] | str:
    """Reemplaza aliases por su nombre canonico (si la tool lo usa).

    Reglas:
      - Solo se remapea si la tool declara el canonico en su schema.
      - Si dos claves (canonica o alias) normalizan al mismo canonico
        con el MISMO valor, se colapsan sin error (idempotente).
      - Si normalizan al mismo canonico con valores DISTINTOS, se
        devuelve un str de error (D7, auditoria 2026-09-26). Antes
        se descartaba el alias en silencio y ganaba el primero.
      - Si la clave no esta en la tabla, se mantiene tal cual
        (y la validacion posterior la rechaza si no esta en schema).

    Devuelve un `dict` (caso normal) o un `str` empezando por
    "ERROR:" (colision con valores distintos). El llamante debe
    comprobar `isinstance(arguments, str)` antes de usar el dict.
    """
    if not arguments:
        return arguments
    properties = spec.get("properties", {})
    if not properties:
        return arguments
    # Mapa alias -> canonico, solo para canonicos que esta tool usa.
    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        if canonical not in properties:
            continue
        for a in aliases:
            alias_to_canonical[a] = canonical
    if not alias_to_canonical:
        return arguments

    out: dict[str, Any] = {}
    # Nombre original (alias o canonico) que aporto cada valor, para
    # el mensaje de error si hay colision con valores distintos.
    origin: dict[str, str] = {}
    for k, v in arguments.items():
        canonical = alias_to_canonical.get(k, k)
        if canonical in out:
            if out[canonical] == v:
                # Mismo valor por dos alias distintos: benigno.
                continue
            return (
                f"ERROR: argumentos en conflicto para {canonical!r}: "
                f"{origin[canonical]!r} y {k!r} tienen valores distintos."
            )
        out[canonical] = v
        origin[canonical] = k
    return out
'''

start = "def _normalise_args(\n"
end = "\ndef _matches_type("
i = src.index(start)
j = src.index(end, i)
src = src[:i] + new_fn.rstrip("\n") + "\n" + src[j + 1 :]

p.write_text(src, encoding="utf-8")
print("OK: D7 aplicado a core/tools.py")
