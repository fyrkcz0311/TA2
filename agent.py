"""Bucle de function calling para procesar correos con un LLM compatible."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, NamedTuple

from dotenv import load_dotenv
import openai

import mock_services
from mock_services import TOOL_REGISTRY
from tools import SYSTEM_PROMPT, get_tools


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
MAX_ITERATIONS = 5
FALSY = {"false", "0", "no", "off"}


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


def _serialize_tool_call(tool_call: Any) -> dict[str, Any]:
    function = getattr(tool_call, "function", None)
    return {
        "id": getattr(tool_call, "id", ""),
        "type": getattr(tool_call, "type", "function"),
        "function": {
            "name": getattr(function, "name", ""),
            "arguments": getattr(function, "arguments", ""),
        },
    }


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


def run_agent(
    email_text: str,
    today_iso: str,
    client: Any | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Procesa un correo, ejecuta sus herramientas justificadas y devuelve la traza."""
    messages: list[dict[str, Any]] = []
    tool_calls_summary: list[dict[str, Any]] = []
    final_response = ""
    error: str | None = None

    try:
        # Los servicios simulados validan contra la misma fecha que ve el modelo.
        mock_services.set_reference_date(today_iso)

        if client is None:
            config = resolve_config()
            client = get_client(config)
            model = model or config.model
        else:
            model = model or DEFAULT_MODEL

        active_tools = get_tools(strict=strict_tools_enabled())
        messages = [
            {
                "role": "system",
                "content": (
                    SYSTEM_PROMPT
                    + "\n\nFECHA ACTUAL: "
                    + today_iso
                    + " (zona horaria America/Lima)"
                ),
            },
            {"role": "user", "content": "CORREO ENTRANTE:\n\n" + email_text},
        ]

        for _ in range(MAX_ITERATIONS):
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=active_tools,
                tool_choice="auto",
                temperature=0,
            )
            message = completion.choices[0].message
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            content = getattr(message, "content", None)

            assistant_message: dict[str, Any] = {
                "role": getattr(message, "role", "assistant"),
                "content": content,
            }
            # Un assistant message con "tool_calls": [] es inválido para la API,
            # así que la clave solo se incluye cuando hay llamadas reales.
            if tool_calls:
                assistant_message["tool_calls"] = [
                    _serialize_tool_call(call) for call in tool_calls
                ]
            messages.append(assistant_message)

            # La presencia de tool_calls es la única señal fiable: algunos
            # proveedores (DeepSeek) devuelven finish_reason="stop" con llamadas.
            if not tool_calls:
                final_response = content or ""
                break

            for serialized_call in assistant_message["tool_calls"]:
                name = serialized_call["function"]["name"]
                arguments, result = _execute_tool(
                    name, serialized_call["function"]["arguments"]
                )
                tool_calls_summary.append(
                    {"name": name, "arguments": arguments, "result": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": serialized_call["id"],
                        "content": json.dumps(result, ensure_ascii=False),
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
        error = str(exc)
    except openai.AuthenticationError as exc:
        error = f"El proveedor rechazó las credenciales (revisa LLM_API_KEY): {exc}"
    except openai.APIConnectionError as exc:
        error = f"No se pudo conectar con el proveedor (revisa LLM_BASE_URL): {exc}"
    except openai.APIStatusError as exc:
        error = f"El proveedor devolvió un error HTTP {exc.status_code}: {exc}"
    except Exception as exc:
        error = f"Error inesperado: {type(exc).__name__}: {exc}"

    if error is not None:
        final_response = ""

    return {
        "final_response": final_response,
        "tool_calls": tool_calls_summary,
        "messages": messages,
        "error": error,
    }
