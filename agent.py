"""Bucle de function calling para procesar correos con un LLM compatible."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, NamedTuple

from dotenv import load_dotenv
import openai

import mock_services
from mock_services import TOOL_REGISTRY
from safety import redact_text, redact_value, sanitize_email
from tools import SYSTEM_PROMPT, get_response_tools


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
MAX_ITERATIONS = 5
FALSY = {"false", "0", "no", "off"}

SECCIONES = ("RESUMEN", "ACCIONES EJECUTADAS", "PENDIENTES", "SIGUIENTE PASO SUGERIDO")

# Identificadores que devuelven los servicios: VENTAS-101, evt_a1b2c3d4, crm_a1b2c3d4.
ID_PATTERN = re.compile(r"\b(?:VENTAS|PROY)-\d+\b|\b(?:evt|crm)_[0-9a-f]{8}\b")

CORRECCION = (
    "VERIFICACIÓN AUTOMÁTICA FALLIDA. Se detectaron estas inconsistencias u "
    "omisiones: {detalle} Solo cuenta como ejecutada una acción si invocaste "
    "la herramienta y recibiste su identificador. Invoca las herramientas "
    "justificadas o reescribe el resumen y registra en PENDIENTES lo que no "
    "pudiste hacer."
)


class ConfigError(RuntimeError):
    """La configuración del proveedor LLM está incompleta."""


class Config(NamedTuple):
    api_key: str
    base_url: str
    model: str


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is not None:
        return env
    load_dotenv()
    return os.environ


def resolve_config(env: Mapping[str, str] | None = None) -> Config:
    """Lee la configuración del proveedor y falla temprano si falta la clave."""
    values = _env(env)
    api_key = (values.get("LLM_API_KEY") or "").strip()
    if not api_key:
        raise ConfigError(
            "Falta LLM_API_KEY. Copia .env.example como .env y completa la clave."
        )
    return Config(
        api_key=api_key,
        base_url=(values.get("LLM_BASE_URL") or DEFAULT_BASE_URL).strip(),
        model=(values.get("LLM_MODEL") or DEFAULT_MODEL).strip(),
    )


def strict_tools_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Indica si se envía "strict" en los schemas; desactivable por proveedor."""
    values = _env(env)
    return (values.get("LLM_STRICT_TOOLS") or "true").strip().lower() not in FALSY


def get_client(config: Config | None = None) -> openai.OpenAI:
    """Construye el cliente LLM a partir de las variables de entorno."""
    config = config or resolve_config()
    return openai.OpenAI(api_key=config.api_key, base_url=config.base_url)


def _execute_tool(name: str, raw_arguments: str) -> tuple[Any, dict[str, Any]]:
    """Valida y ejecuta una herramienta, devolviendo (argumentos, resultado)."""
    try:
        arguments = json.loads(raw_arguments)
    except (TypeError, ValueError):
        return raw_arguments, {"ok": False, "error": "argumentos JSON inválidos"}

    if not isinstance(arguments, dict):
        return arguments, {"ok": False, "error": "los argumentos deben ser un objeto"}
    if name not in TOOL_REGISTRY:
        return arguments, {"ok": False, "error": "herramienta desconocida"}
    try:
        return arguments, TOOL_REGISTRY[name](**arguments)
    except TypeError as exc:
        return arguments, {"ok": False, "error": f"parámetros inválidos: {exc}"}


def _response_text(items: list[dict[str, Any]]) -> str:
    """Extrae el texto final de los items de Responses sin incluir razonamiento."""
    return "\n".join(
        part.get("text", "")
        for item in items
        if item.get("type") == "message" and item.get("role") == "assistant"
        for part in item.get("content", [])
        if isinstance(part, dict) and part.get("type") == "output_text"
    ).strip()


def detect_missing_actions(email_text: str, tool_calls: list[dict[str, Any]]) -> list[str]:
    """Señala requisitos explícitos que el modelo dejó sin registrar."""
    problems: list[str] = []
    sender = re.search(r"(?im)^De:\s*[^\s@]+@([^\s@]+\.[^\s@]+)", email_text)
    has_sender_email = sender is not None
    identified_contact = re.search(
        r"\b(?:somos|saludos[,\s]+)\s*[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+"
        r"[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+de\s+[A-ZÁÉÍÓÚÑ][\w-]+",
        email_text,
        re.IGNORECASE,
    ) is not None
    has_requirement = re.search(
        r"\b(?:necesitamos|solicitamos|requerimos|queremos)\s+que\b|"
        r"\bdebe\s+(?:permitir|incluir|mostrar|generar|implementar)\b",
        email_text,
        re.IGNORECASE,
    ) is not None
    ticket_created = any(
        call.get("name") == "crear_ticket_en_jira" and call["result"].get("ok") is True
        for call in tool_calls
    )
    company_from_domain = bool(sender) and sender.group(1).split(".")[0].lower() not in {
        "gmail", "hotmail", "outlook", "yahoo", "icloud",
    }
    if has_requirement and (company_from_domain or identified_contact) and not ticket_created:
        problems.append("El correo contiene un requisito funcional explícito y no se creó un ticket.")

    crm_updated = any(
        call.get("name") == "actualizar_contacto_en_crm" and call["result"].get("ok") is True
        for call in tool_calls
    )
    if has_sender_email and identified_contact and has_requirement and not crm_updated:
        problems.append("El remitente está identificado y expresa una necesidad concreta, pero no se actualizó el CRM.")
    return problems


def _seccion(text: str, header: str) -> str | None:
    """Extrae el cuerpo de una sección del resumen, o None si no existe."""
    inicio = text.find(header)
    if inicio == -1:
        return None
    cuerpo = text[inicio + len(header):].lstrip(":").lstrip()
    posiciones = [
        cuerpo.find(otra) for otra in SECCIONES if otra != header and cuerpo.find(otra) != -1
    ]
    return cuerpo[: min(posiciones)].strip() if posiciones else cuerpo.strip()


def detect_inconsistencies(
    final_response: str, tool_calls: list[dict[str, Any]]
) -> list[str]:
    """Compara lo que el resumen afirma contra lo que realmente se ejecutó.

    El riesgo principal del sistema es que el modelo redacte "ticket creado" sin
    haber invocado la herramienta: el equipo interno daría por hecha una acción
    inexistente. Esta verificación es determinista y no depende del modelo.
    """
    seccion = _seccion(final_response, "ACCIONES EJECUTADAS")
    if seccion is None:
        return ["Falta la sección ACCIONES EJECUTADAS; no se puede verificar el resumen."]

    ejecutados = {
        call["result"]["id"]
        for call in tool_calls
        if isinstance(call.get("result"), dict)
        and call["result"].get("ok")
        and call["result"].get("id")
    }
    declara_ninguna = re.fullmatch(r"[-\s]*ninguna\.?\s*", seccion, re.IGNORECASE) is not None

    problemas: list[str] = []
    if seccion and not declara_ninguna and not ejecutados:
        problemas.append(
            "El resumen declara acciones ejecutadas pero no se invocó ninguna "
            "herramienta con éxito."
        )

    inventados = set(ID_PATTERN.findall(seccion)) - ejecutados
    if inventados:
        problemas.append(
            "El resumen menciona identificadores que ningún servicio devolvió: "
            + ", ".join(sorted(inventados))
            + "."
        )
    if declara_ninguna:
        if ejecutados:
            problemas.append("El resumen declara Ninguna pero sí hay acciones ejecutadas.")
        return problemas

    if not seccion:
        problemas.append("La sección ACCIONES EJECUTADAS está vacía.")
    tipos = {
        "crear_ticket_en_jira": r"\b(?:tickets?|jira|incidencias?)\b",
        "agendar_reunion_en_google_calendar": r"\b(?:reuni[oó]n|reuniones|eventos?|calendar|demo|llamada)\b",
        "actualizar_contacto_en_crm": r"\b(?:contactos?|crm)\b",
    }
    ids_por_tipo = {
        name: {call["result"]["id"] for call in tool_calls
               if call.get("name") == name and isinstance(call.get("result"), dict)
               and call["result"].get("ok") is True and call["result"].get("id")}
        for name in tipos
    }
    for linea in seccion.splitlines():
        if not linea.strip():
            continue
        ids = set(ID_PATTERN.findall(linea))
        if not ids.intersection(ejecutados):
            problemas.append("Acción sin identificador de una ejecución exitosa: " + linea.strip())
        for name, patron in tipos.items():
            if re.search(patron, linea, re.IGNORECASE) and not ids.intersection(ids_por_tipo[name]):
                problemas.append(f"La acción descrita no tiene un identificador válido de {name}: {linea.strip()}")
    return problemas


def run_agent(
    email_text: str,
    today_iso: str,
    client: Any | None = None,
    model: str | None = None,
    service_state: mock_services.ServiceState | None = None,
) -> dict[str, Any]:
    """Cada ejecución usa estado propio, o el de la sesión que la invoca."""
    state = service_state if service_state is not None else mock_services.ServiceState()
    with mock_services.service_session(state):
        result = _run_agent(email_text, today_iso, client, model)
        result["audit_log"] = mock_services.get_execution_log()
        return result


def _run_agent(
    email_text: str,
    today_iso: str,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Procesa un correo, ejecuta sus herramientas justificadas y devuelve la traza."""
    messages: list[dict[str, Any]] = []
    tool_calls_summary: list[dict[str, Any]] = []
    final_response = ""
    warnings: list[str] = []
    error: str | None = None
    correccion_pedida = False
    known_secrets: tuple[str, ...] = ()

    try:
        # Los servicios simulados validan contra la misma fecha que ve el modelo.
        mock_services.set_reference_date(today_iso)

        if client is None:
            config = resolve_config()
            client = get_client(config)
            model = model or config.model
        else:
            model = model or DEFAULT_MODEL

        safe_email, known_secrets = sanitize_email(email_text)
        active_tools = get_response_tools(strict=strict_tools_enabled())
        instructions = SYSTEM_PROMPT + "\n\nFECHA ACTUAL: " + today_iso + " (zona horaria America/Lima)"
        messages = [
            {"role": "user", "content": "CORREO ENTRANTE:\n\n" + safe_email},
        ]

        for _ in range(MAX_ITERATIONS):
            response = client.responses.create(
                model=model,
                instructions=instructions,
                input=messages,
                tools=active_tools,
                tool_choice="auto",
                temperature=0,
            )
            if getattr(response, "status", "completed") in {"failed", "incomplete"}:
                error = "El proveedor no completó la respuesta; revisa sus límites o inténtalo de nuevo."
                break
            output = redact_value(response.model_dump().get("output", []), known_secrets)
            messages.extend(output)
            tool_calls = [item for item in output if item.get("type") == "function_call"]
            if not tool_calls:
                candidato = _response_text(output)
                problemas = (
                    detect_inconsistencies(candidato, tool_calls_summary)
                    + detect_missing_actions(safe_email, tool_calls_summary)
                )
                # Se le da una sola oportunidad de corregirse; si insiste, la
                # advertencia viaja hasta la interfaz en lugar de silenciarse.
                if problemas and not correccion_pedida:
                    correccion_pedida = True
                    messages.append(
                        {
                            "role": "user",
                            "content": CORRECCION.format(detalle=" ".join(problemas))
                            + " Si omitiste un requisito funcional explícito, crea su ticket "
                            "con la información conocida y deja las dudas en PENDIENTES.",
                        }
                    )
                    continue
                final_response = candidato
                warnings = problemas
                break

            for call in tool_calls:
                name = call.get("name", "")
                arguments, result = _execute_tool(
                    name, call.get("arguments", "")
                )
                tool_calls_summary.append(
                    {"name": name, "arguments": arguments, "result": result}
                )
                messages.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.get("call_id", ""),
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
        else:
            error = (
                f"El agente excedió el máximo de {MAX_ITERATIONS} iteraciones "
                "sin producir una respuesta final."
            )
            final_response = ""

        if not final_response and error is None:
            error = "El modelo no devolvió una respuesta final para el equipo interno."

    except ConfigError as exc:
        error = redact_text(str(exc), known_secrets)
    except openai.AuthenticationError as exc:
        error = redact_text(f"El proveedor rechazó las credenciales (revisa LLM_API_KEY): {exc}", known_secrets)
    except openai.APIConnectionError as exc:
        error = redact_text(f"No se pudo conectar con el proveedor (revisa LLM_BASE_URL): {exc}", known_secrets)
    except openai.APIStatusError as exc:
        error = redact_text(f"El proveedor devolvió un error HTTP {exc.status_code}: {exc}", known_secrets)
    except Exception as exc:
        error = redact_text(f"Error inesperado: {type(exc).__name__}: {exc}", known_secrets)

    if error is not None:
        final_response = ""

    return {
        "final_response": final_response,
        "tool_calls": tool_calls_summary,
        "messages": messages,
        "warnings": warnings,
        "error": error,
    }
