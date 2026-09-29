"""Heurística de intención para el uso de herramientas.

Decide, a partir del último mensaje del usuario, si procede ofrecer
herramientas al modelo y si una llamada de herramienta concreta se
corresponde con lo que el usuario pidió.

Importante: esto NO es un mecanismo de seguridad. Es un filtro
anti-alucinación para reducir llamadas de herramientas no solicitadas.
El control de seguridad real frente a operaciones destructivas es la
confirmación explícita en la interfaz.

Las reglas concretas de cada herramienta las declara su ``ToolProvider``
a través del método ``intent_rules()``. Aquí vive solo la maquinaria.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import ClassVar

import simplemma

# F7-quater (2026-09-28): conjunto de tools de solo lectura.
# Con piloto automatico activo (auto_approve=True), el gate NO
# bloquea estas tools. Razon: leer/cargar/inspeccionar no tiene
# efectos secundarios; pedir al modelo que "consulte" al usuario
# por una lectura es friccion sin valor cuando el usuario ya ha
# autorizado tools sin dialogo.
#
# El set es explicito a proposito: NO se usa un prefijo "read_"
# porque podria haber tools con ese prefijo que no sean puras
# (ej. "read_then_delete"). La lista es corta y manual. Si anades
# una tool MCP nueva de solo lectura, anadela aqui.
READ_ONLY_TOOLS: frozenset[str] = frozenset({
    # Nucleo.
    "leer_archivo",
    "listar_carpeta",
    "buscar_en_workspace",
    "buscar_simbolo",
    "rag_query",
    # Git (solo lectura).
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    # MCP filesystem: solo lectura.
    "mcp__fs__read_file",
    "mcp__fs__read_text_file",
    "mcp__fs__read_media_file",
    "mcp__fs__read_multiple_files",
    "mcp__fs__list_directory",
    "mcp__fs__list_directory_with_sizes",
    "mcp__fs__directory_tree",
    "mcp__fs__search_files",
    "mcp__fs__get_file_info",
    "mcp__fs__list_allowed_directories",
})


def is_read_only(name: str) -> bool:
    """True si la tool es de solo lectura (sin efectos secundarios).

    Usado por authorize_and_execute para saltar el gate cuando el
    piloto automatico esta activo. Las tools de escritura NUNCA
    se saltan el gate, aunque auto_approve sea True: el gate
    aplica su propia heuristica de intencion sobre el texto del
    usuario para esas.
    """
    return name in READ_ONLY_TOOLS


# Confirmaciones conversacionales. Cuando el modelo pregunta
# "¿Puedo leer gui.py?" y el usuario responde "si", el texto del
# usuario no contiene verbos de la tool. Sin esta lista, el gate
# bloquea la tool y el modelo repite la pregunta en bucle (P1
# auditoria 2026-09-26).
_CONFIRMATIONS: frozenset[str] = frozenset({
    "si", "sí", "yes",
    "vale", "ok", "okay", "dale", "claro",
    "adelante", "hazlo", "hazla", "confirma", "confirmo",
    "confirmado", "de acuerdo", "por supuesto", "go ahead",
})


# H1 (2026-09-27): continuaciones. El usuario acepta el plan del
# assistant sin nombrar la accion ("sigue", "procede", "aplicalo").
# Se combinan con confirmaciones en el gate: basta con que el
# assistant previo haya mencionado un verbo de la regla.
_CONTINUATIONS: frozenset[str] = frozenset({
    "sigue", "siguelo", "siguela", "siguelos", "siguelas",
    "continua", "continúa", "continualo", "continúalo",
    "procede", "proceda",
    "aplica", "aplicalo", "aplícalo", "aplicala", "aplícala",
})


# X1.3 (auditoria externa 2026-09-29, P1#2): negaciones cortas.
# is_short_confirmation acepta cualquier frase <=4 tokens que
# contenga una palabra de _CONFIRMATIONS. "no, claro" pasaba por
# "claro". Estas palabras al INICIO de la frase la descalifican
# como confirmacion.
_NEGATIONS: frozenset[str] = frozenset({
    "no", "nunca", "jamas", "jamás",
    "tampoco", "nada",
    "cancela", "cancelar", "cancelalo", "cancelala",
    "cancélalo", "cancélala",
    "para", "parar", "paralo", "parala",
    "espera", "esperar", "esperate", "espérate",
    "alto", "stop",
})


# F6 (2026-09-27): post-procesado sobre simplemma. simplemma no
# tiene POS tagging, asi que falla con sustantivos verbales,
# participios irregulares y algunos encliticos.
_POSTPROC: dict[str, str] = {
    "creacion": "crear", "edicion": "editar",
    "actualizacion": "actualizar", "modificacion": "modificar",
    "refactorizacion": "refactorizar", "correccion": "corregir",
    "escritura": "escribir", "implementacion": "implementar",
    "programacion": "programar", "instalacion": "instalar",
    "ejecucion": "ejecutar", "compilacion": "compilar",
    "escrito": "escribir", "hecho": "hacer", "dicho": "decir",
    "visto": "ver", "puesto": "poner", "vuelto": "volver",
    "abierto": "abrir", "cubierto": "cubrir", "muerto": "morir",
    "lanzalo": "lanzar", "buscalo": "buscar",
    "muestralo": "mostrar", "borralo": "borrar",
    "leelo": "leer", "abrelo": "abrir",
    "cierralo": "cerrar", "guardalo": "guardar",
    "arreglalo": "arreglar", "corrigelo": "corregir",
    "actualizalo": "actualizar", "modificalo": "modificar",
    "refactorizalo": "refactorizar",
    # 1a persona singular del presente (usada en preguntas
    # "¿como leo X?", "¿como escribo Y?"). simplemma a veces no
    # reduce estos verbos por colision con nombres propios o
    # homografos.
    "leo": "leer", "escribo": "escribir", "edito": "editar",
    "creo": "crear", "abro": "abrir", "cierro": "cerrar",
    "ejecuto": "ejecutar", "corro": "correr",
    "compilo": "compilar", "instalo": "instalar",
    "busco": "buscar", "encuentro": "encontrar",
    "muestro": "mostrar", "borro": "borrar",
    "modifico": "modificar", "actualizo": "actualizar",
    "arreglo": "arreglar", "corrijo": "corregir",
    "refactorizo": "refactorizar", "implemento": "implementar",
    "anado": "anadir", "añado": "anadir",
    "agrego": "agregar", "incluyo": "incluir",
    "guardo": "guardar", "testeo": "testear",
    # Subjuntivos (presente) comunes en el assistant cuando pregunta
    # "¿Quieres que escriba...?". simplemma no siempre los reduce.
    "escriba": "escribir", "escribas": "escribir",
    "edite": "editar", "edites": "editar",
    "ejecute": "ejecutar", "ejecutes": "ejecutar",
    "lea": "leer", "leas": "leer",
    "vea": "ver", "veas": "ver",
    "haga": "hacer", "hagas": "hacer",
    "cree": "crear", "crees": "crear",
    "modifique": "modificar", "modifiques": "modificar",
    "actualice": "actualizar", "actualices": "actualizar",
    "borre": "borrar", "borres": "borrar",
    "abra": "abrir", "abras": "abrir",
    "cierre": "cerrar", "cierres": "cerrar",
    "corrija": "corregir", "corrijas": "corregir",
    "arregle": "arreglar", "arregles": "arreglar",
    # Formas conjugadas cortas (3a pers. sing. presente, la forma
    # mas comun de imperativo informal en espanol). simplemma las
    # deja sin lematizar porque son ambiguas sin POS tagging.
    "busca": "buscar", "muestra": "mostrar",
    "borra": "borrar", "corre": "correr",
    "lanza": "lanzar", "compila": "compilar",
    "instala": "instalar", "testea": "testear",
    "ejecuta": "ejecutar", "edita": "editar",
    "crea": "crear", "escribe": "escribir",
    "lee": "leer", "abre": "abrir",
    "cierra": "cerrar", "guarda": "guardar",
    "arregla": "arreglar", "corrige": "corregir",
    "actualiza": "actualizar", "modifica": "modificar",
    "refactoriza": "refactorizar", "completa": "completar",
    "implementa": "implementar", "programa": "programar",
    "anade": "anadir", "agrega": "agregar",
    "incluye": "incluir", "desarrolla": "desarrollar",
    # Irregulares sin tilde
    "corrio": "correr", "leyo": "leer",
    "escribio": "escribir", "encontro": "encontrar",
}
from typing import Any

MCP_ACTION_VERBS: tuple[str, ...] = (
    "usa", "usar", "utiliza", "utilizar", "llama", "llamar",
    "invoca", "invocar", "ejecuta", "ejecutar", "realiza", "realizar",
)


# Detecta un nombre de archivo con extensión (por ejemplo "notas.txt").
_FILENAME_PATTERN = re.compile(
    r"\b[\w\-]+\.(?:txt|md|markdown|csv|tsv|json|py|js|ts|jsx|tsx|html|htm|css"
    r"|xml|yaml|yml|log|pdf|docx?|xlsx?|ini|cfg|conf|sh|png|jpe?g|gif|svg|webp|toml)\b",
    re.IGNORECASE,
)


# H16 (2026-09-27): palabras interrogativas en espanol con tilde.
# Lista CORTA y estable. Duplicada de core/ollama.py para evitar
# import circular (ollama importa intent, no al reves).
_QUESTION_MARKERS: tuple[str, ...] = (
    "cómo", "qué", "cuál", "cuáles",
    "cuándo", "dónde", "quién", "quiénes",
    "por qué", "cuánto", "cuánta", "cuántos", "cuántas",
)


def _looks_like_informative_question(text: str) -> bool:
    """True si el texto parece una pregunta informativa.

    H16 (2026-09-27): el exposure gate no debe exponer tools cuando
    el usuario pregunta cosas como "¿como leo un archivo en Python?"
    — eso es conocimiento general, no una peticion sobre el workspace.

    Excepcion: si el texto contiene un filename explicito
    ("main.py"), la pregunta se considera peticion real
    ("¿puedes leer main.py?").
    """
    if not text:
        return False
    s = text.strip()
    if not s:
        return False
    # Filename explicito -> no es pregunta informativa.
    if _FILENAME_PATTERN.search(s):
        return False
    if s.endswith(("?", "？")):
        return True
    if s.startswith("¿"):
        return True
    lower = s.lower()
    return any(m in lower for m in _QUESTION_MARKERS)


def _strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", text.lower())
        if unicodedata.category(char) != "Mn"
    )


def _lemma(word: str) -> str:
    """Lematiza una palabra al espanol, sin acentos.

    F6 (2026-09-27): sustituye al stemmer propio. Aplica simplemma
    + post-procesado (_POSTPROC).
    """
    if not word:
        return ""
    norm = _strip_accents(word.strip())
    if not norm:
        return ""
    if norm in _POSTPROC:
        return _POSTPROC[norm]
    try:
        lemma = simplemma.lemmatize(word.lower().strip(), lang="es")
    except Exception:
        return norm
    norm_lemma = _strip_accents(lemma.strip())
    if not norm_lemma:
        return norm
    return _POSTPROC.get(norm_lemma, norm_lemma)


@dataclass(frozen=True)
class IntentRule:
    """Reglas de intención para una herramienta concreta.

    Una regla vacía (``IntentRule()``) nunca autoriza la herramienta.
    """

    # Verbos que activan la herramienta siempre, sin necesidad de
    # target ("crea", "lee", "diff").
    verbs: tuple[str, ...] = ()

    # Verbos débiles: solo activan la herramienta si además aparece
    # un target_word o un filename válido. Sirven para distinguir
    # "¿dónde está X del proyecto?" (autoriza) de "¿dónde está la
    # capital de Asturias?" (no autoriza, es pregunta general).
    weak_verbs: tuple[str, ...] = ()

    # Palabras-objetivo del dominio de la herramienta ("archivo", "repo").
    target_words: tuple[str, ...] = ()

    # Si es True (por defecto), además del verbo debe aparecer una
    # palabra-objetivo o un nombre de archivo (si ``accepts_filename``).
    requires_target: bool = True

    # Si es True, un nombre de archivo con extensión cuenta como target válido.
    accepts_filename: bool = False

    # Si es True, esta herramienta puede autorizarse como paso previo cuando
    # el usuario pide escribir/editar/actualizar algo.
    is_read_prerequisite: bool = False

    # Regla especial para herramientas MCP genéricas.
    mcp_explicit_name_required: bool = False


class ToolIntentGate:
    """Evalúa si una petición autoriza exponer/ejecutar una herramienta.

    Tanto el texto del usuario como las palabras declaradas en las reglas
    se normalizan (minúsculas + sin acentos) antes de compararse. Así una
    regla puede declarar ``"dónde"`` y matchear ``"¿dónde está X?"`` aunque
    el usuario escriba con tilde o sin ella.
    """

    _RULES_REGISTRY: ClassVar[dict[str, IntentRule]] = {}

    def __init__(self, rules: dict[str, IntentRule] | None = None):
        self.rules: dict[str, IntentRule] = dict(rules or {})

    # -- API pública ---------------------------------------------------------

    def tools_for_request(
        self,
        tools: list[dict[str, Any]] | None,
        text: str,
        *,
        last_assistant: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """True si hay que exponer las tools al modelo este turno.

        H1 (2026-09-27): acepta `last_assistant` para el flujo
        multi-turno. Si el usuario responde con confirmacion
        ("vale") o continuacion ("sigue", "procede") y el assistant
        previo propuso una accion (mencionando verbos de alguna
        regla), expone las tools. Antes, esa respuesta corta no
        matcheaba ningun verbo y el gate bloqueaba todo.
        """
        if not tools:
            return None
        # 1. H1: confirmacion/continuacion con assistant previo.
        # Va primero para que "sigue" gane aunque parezca pregunta.
        if (
            last_assistant
            and (
                self.is_short_confirmation(text)
                or self.is_short_continuation(text)
            )
        ):
            for rule in self.rules.values():
                if rule.mcp_explicit_name_required:
                    continue
                if self._assistant_mentions_rule_verb(rule, last_assistant):
                    return tools
        # 2. H16: pregunta informativa sin filename -> no exponer.
        # Ahorra ~1000 tokens de tool_prompt en preguntas generales
        # ("¿como leo un archivo?") que no son peticiones.
        if _looks_like_informative_question(text):
            return None
        # 3. Camino normal: menciones directas al workspace o MCP.
        #    H1-bis: pasa last_assistant para anafora pura.
        if (
            self._mentions_workspace_operation(
                text, last_assistant=last_assistant,
            )
            or self._mentions_mcp_tool(text, tools)
        ):
            return tools
        return None

    @staticmethod
    def is_short_confirmation(text: str) -> bool:
        """True si el texto es una confirmacion corta ("si", "vale"...).

        Tolerante a mayusculas, puntuacion final, y frases cortas
        del tipo "si, por favor" o "ok, adelante".
        """
        if not text:
            return False
        norm = text.strip().lower().rstrip(".!,;:")
        if not norm:
            return False
        # X1.3: si la frase empieza por negacion, no es confirmacion
        # aunque contenga una palabra de _CONFIRMATIONS ("no, claro").
        tokens = norm.split()
        if tokens and tokens[0].strip(".!,;:") in _NEGATIONS:
            return False
        if norm in _CONFIRMATIONS:
            return True
        if len(tokens) <= 4 and any(
            t.strip(".!,;:") in _CONFIRMATIONS for t in tokens
        ):
            return True
        return False

    @staticmethod
    def is_short_continuation(text: str) -> bool:
        """True si el texto indica continuar con la accion previa.

        H1 (2026-09-27): "sigue", "procede", "aplicalo" aceptan el
        plan propuesto por el assistant sin repetir el verbo.
        Misma tolerancia que is_short_confirmation.
        """
        if not text:
            return False
        norm = text.strip().lower().rstrip(".!,;:")
        if not norm:
            return False
        if norm in _CONTINUATIONS:
            return True
        tokens = norm.split()
        if len(tokens) <= 4 and any(
            t.strip(".!,;:") in _CONTINUATIONS for t in tokens
        ):
            return True
        return False

    def _assistant_mentions_rule_verb(
        self, rule: IntentRule, assistant_text: str
    ) -> bool:
        """True si el assistant menciono algun verbo de la regla.

        H1 (2026-09-27): compara por lemas (simplemma + _POSTPROC).
        Antes usaba substring directo, que fallaba con conjugaciones
        ("ejecuto" no contiene "ejecuta" ni "ejecutar"). Los lemas
        cubren conjugaciones, encliticos y sustantivos verbales.

        Intencionalmente mas laxo que _mentions_any_word: aqui el
        assistant es el que PREGUNTA, no el que ejecuta; la
        autorizacion real la da el usuario con la confirmacion o
        continuacion (P1 auditoria 2026-09-26, H1 2026-09-27).
        """
        if not assistant_text:
            return False
        all_verbs = rule.verbs + rule.weak_verbs
        if not all_verbs:
            return False
        verb_lemmas = {_lemma(v) for v in all_verbs}
        verb_lemmas.discard("")
        if not verb_lemmas:
            return False
        norm = self._normalise(assistant_text).lower()
        for token in re.findall(r"\w+", norm, re.UNICODE):
            if _lemma(token) in verb_lemmas:
                return True
        return False

    def tool_is_requested(
        self,
        name: str,
        text: str,
        *,
        last_assistant: str | None = None,
    ) -> bool:
        """Decide si el texto autoriza la herramienta ``name``.

        Si `last_assistant` se proporciona y el usuario responde con
        una confirmacion corta ("si", "vale", "ok"...), se autoriza
        la tool si el assistant anterior menciono algun verbo de la
        regla. Cubre el caso "modelo pregunta -> usuario confirma"
        sin tener que reescribir el prompt (P1 auditoria 2026-09-26).
        """
        rule = self.rules.get(name)
        if rule is None:
            if name.startswith("mcp__"):
                rule = IntentRule(mcp_explicit_name_required=True)
            else:
                return False

        # Confirmacion conversacional: el usuario responde con un
        # texto corto tipo "si" / "vale" a una pregunta del modelo.
        # Se autoriza si el assistant anterior menciono un verbo de
        # esta regla. Se comprueba ANTES de la negacion para no
        # bloquear un "si, hazlo" por error.
        if (
            last_assistant
            and (
                self.is_short_confirmation(text)
                or self.is_short_continuation(text)
            )
            and self._assistant_mentions_rule_verb(rule, last_assistant)
        ):
            return True

        all_verbs = rule.verbs + rule.weak_verbs
        if self._is_negated(all_verbs, text):
            return False

        if rule.mcp_explicit_name_required:
            return self._mcp_explicit_request(name, text)

        normalised = self._normalise(text)

        # weak_verbs: autorizan solo si hay target. Se evalúan antes
        # del chequeo de requires_target.
        if rule.weak_verbs and self._mentions_any_word(
            normalised, rule.weak_verbs
        ):
            if self._mentions_target_ext(
                rule.target_words, text, normalised,
                rule.accepts_filename,
                last_assistant=last_assistant,
            ):
                return True
            # Verbo débil sin target: no autoriza por esta vía. Pero
            # podría autorizar por verbo fuerte. Caer al chequeo
            # final.

        if rule.requires_target and not self._mentions_target_ext(
            rule.target_words, text, normalised, rule.accepts_filename,
            last_assistant=last_assistant,
        ):
            return False

        if self._mentions_any_word(normalised, rule.verbs):
            return True

        if rule.is_read_prerequisite and self._mentions_any_word(
            normalised, self._read_prerequisite_verbs()
        ):
            return True
        return False

    # -- negación ------------------------------------------------------------

    @classmethod
    def _is_negated(cls, verbs: tuple[str, ...], text: str) -> bool:
        """Detecta si la petición principal niega explícitamente la operación.

        Regla: cuenta como negación solo si NINGÚN verbo del conjunto
        aparece en forma afirmativa. Si hay al menos una aparición
        afirmativa, las negaciones de otros verbos del mismo conjunto
        se interpretan como restricciones secundarias, no como bloqueo.

        Ejemplos:
            "Crea un archivo. No añadas funciones."
              → "crea" es afirmativo. NO se bloquea.
            "No crees el archivo todavía."
              → "crees" solo aparece negado. Se bloquea.
            "no vas a borrar archivo.txt"
              → "borrar" solo aparece negado (con "vas a" en medio).
                Se bloquea.
        """
        if not verbs:
            return False
        normalised = cls._normalise(text)
        verb_lemmas = {_lemma(cls._normalise(v)) for v in verbs}
        verb_lemmas.discard("")
        if not verb_lemmas:
            return False

        tokens = re.findall(r"\w+", normalised, re.UNICODE)
        if not tokens:
            return False
        lemmas = [_lemma(t) for t in tokens]

        def has_no_before(idx: int, max_gap: int = 4) -> bool:
            for gap in range(1, max_gap + 1):
                j = idx - gap
                if j < 0:
                    return False
                if tokens[j] == "no":
                    return True
            return False

        # Paso 1: hay alguna forma afirmativa?
        for i, lm in enumerate(lemmas):
            if lm in verb_lemmas and not has_no_before(i):
                return False

        # Paso 2: sin afirmativa, hay negacion?
        for i, lm in enumerate(lemmas):
            if lm in verb_lemmas and has_no_before(i):
                return True
        return False

    @classmethod
    def _mcp_explicit_request(cls, name: str, text: str) -> bool:
        normalised = cls._normalise(text)
        direct_name = cls._normalise(name)
        if not re.search(rf"\b{re.escape(direct_name)}\b", normalised):
            return False
        if cls._is_negated(MCP_ACTION_VERBS, text):
            return False
        return cls._mentions_any_word(normalised, MCP_ACTION_VERBS)

    # -- detección de palabras ----------------------------------------------

    @classmethod
    def _mentions_any_word(cls, normalised: str, words: tuple[str, ...]) -> bool:
        """Cierto si alguna de ``words`` aparece con fronteras de palabra.

        Las palabras se normalizan (minúsculas + sin acentos) antes de
        compararlas. ``\\b`` respeta la transición entre un carácter no-letra
        ("¿", ",", "?") y una letra, así que "¿dónde" y "dónde?" matchean
        la palabra normalizada "donde".
        """
        # F6 (2026-09-27): comparacion por lemas.
        text_lemmas = {
            _lemma(t)
            for t in re.findall(r"\w+", normalised, re.UNICODE)
        }
        if not text_lemmas:
            return False
        for word in words:
            w_lemma = _lemma(cls._normalise(word))
            if w_lemma and w_lemma in text_lemmas:
                return True
        return False

    @classmethod
    def _mentions_target(
        cls,
        words: tuple[str, ...],
        text: str,
        normalised: str,
        accepts_filename: bool,
    ) -> bool:
        if words and cls._mentions_any_word(normalised, words):
            return True
        if accepts_filename and _FILENAME_PATTERN.search(text):
            return True
        return False

    @classmethod
    def _mentions_target_ext(
        cls,
        words: tuple[str, ...],
        text: str,
        normalised: str,
        accepts_filename: bool,
        *,
        last_assistant: str | None = None,
    ) -> bool:
        """H1-bis (2026-09-27): target en el texto o en el assistant
        previo. Cubre la anafora pura:

            user: "lee gui.py"
            assistant: "Aqui tienes el contenido de gui.py..."
            user: "ahora edítalo"     <- sin target, pero el filename
                                        esta en el assistant previo.

        En ese flujo, `_mentions_target` falla porque "ahora edítalo"
        no menciona ni target_words ni filename. Con _ext, se mira
        tambien `last_assistant` para encontrar el target.
        """
        if cls._mentions_target(words, text, normalised, accepts_filename):
            return True
        if not last_assistant:
            return False
        return cls._mentions_target(
            words, last_assistant,
            cls._normalise(last_assistant), accepts_filename,
        )

    # -- operaciones de workspace -------------------------------------------

    def _mentions_workspace_operation(
        self,
        text: str,
        *,
        last_assistant: str | None = None,
    ) -> bool:
        """True si el texto sugiere alguna operación sobre el workspace.

        Usa las reglas de la instancia (las del provider que construyó
        el gate), no un registro global. Así dos gates con reglas
        distintas no se contaminan mutuamente.

        H1-bis (2026-09-27): acepta `last_assistant` para la anafora
        pura ("ahora edítalo" con filename en el assistant previo).
        """
        normalised = self._normalise(text)
        for rule in self.rules.values():
            if rule.mcp_explicit_name_required:
                continue
            # weak_verbs: autorizan SOLO si hay target. Se evalúan
            # antes del chequeo de requires_target porque tienen
            # semántica propia: "verbo débil + target" → sí;
            # "verbo débil sin target" → no (pero sigue probando
            # los verbs fuertes).
            if rule.weak_verbs:
                has_target = self._mentions_target_ext(
                    rule.target_words, text, normalised,
                    rule.accepts_filename,
                    last_assistant=last_assistant,
                )
                if has_target and self._mentions_any_word(
                    normalised, rule.weak_verbs
                ):
                    return True
            if rule.requires_target and not self._mentions_target_ext(
                rule.target_words, text, normalised, rule.accepts_filename,
                last_assistant=last_assistant,
            ):
                continue
            if self._mentions_any_word(normalised, rule.verbs):
                return True
        return False

    @classmethod
    def _mentions_mcp_tool(cls, text: str, tools: list[dict[str, Any]]) -> bool:
        normalised = cls._normalise(text)
        if not cls._mentions_any_word(normalised, MCP_ACTION_VERBS):
            return False
        for item in tools:
            name = str(item.get("function", {}).get("name", ""))
            if not name:
                continue
            direct_name = cls._normalise(name)
            if re.search(rf"\b{re.escape(direct_name)}\b", normalised):
                return True
        return False

    # -- registro global -----------------------------------------------------

    @classmethod
    def register_rules(cls, rules: dict[str, IntentRule]) -> None:
        """Registra reglas de forma global.

        DEPRECADO. Existe solo como fallback para compatibilidad con
        tests que pasan listas de definiciones (no providers) a
        ``OllamaClient.chat()``. El registro es global al proceso y
        crece monotónicamente: no hay forma de deshacerlo.

        Un uso correcto pasa un ToolProvider con ``intent_rules()``.
        Cada gate nuevo usa esas reglas vía ``self.rules``.
        """
        cls._RULES_REGISTRY.update(rules)

    @classmethod
    def _read_prerequisite_verbs(cls) -> tuple[str, ...]:
        return (
            "escribe", "escribir", "edita", "editar", "actualiza", "actualizar",
            "reemplaza", "reemplazar", "cambia", "cambiar", "modifica", "modificar",
        )

    # -- normalización -------------------------------------------------------

    @staticmethod
    def _normalise(text: str) -> str:
        return _strip_accents(text)
