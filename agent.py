"""Bucle de function calling para procesar correos con un LLM compatible."""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
import openai

from mock_services import TOOL_REGISTRY
from tools import SYSTEM_PROMPT, TOOLS


def get_client() -> openai.OpenAI:
    """Construye el cliente LLM a partir de las variables de entorno."""
    load_dotenv()
    return openai.OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
    )


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


def run_agent(email_text: str, today_iso: str) -> dict[str, Any]:
    """Procesa un correo, ejecuta sus herramientas justificadas y devuelve la traza."""
    messages: list[dict[str, Any]] = []
    tool_calls_summary: list[dict[str, Any]] = []
    final_response = ""
    error: str | None = None

    try:
        client = get_client()
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

        for _ in range(5):
            completion = client.chat.completions.create(
                model=os.getenv("LLM_MODEL"),
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0,
            )
            choice = completion.choices[0]
            message = choice.message
            tool_calls = list(getattr(message, "tool_calls", None) or [])
            serialized_tool_calls = [_serialize_tool_call(call) for call in tool_calls]
            content = getattr(message, "content", None)
            messages.append(
                {
                    "role": getattr(message, "role", "assistant"),
                    "content": content,
                    "tool_calls": serialized_tool_calls,
                }
            )

            if getattr(choice, "finish_reason", None) != "tool_calls" or not tool_calls:
                final_response = content or ""
                break

            for serialized_call in serialized_tool_calls:
                call_id = serialized_call["id"]
                name = serialized_call["function"]["name"]
                raw_arguments = serialized_call["function"]["arguments"]
                try:
                    arguments = json.loads(raw_arguments)
                except (TypeError, ValueError):
                    arguments = raw_arguments
                    result = {"ok": False, "error": "argumentos JSON inválidos"}
                else:
                    if name not in TOOL_REGISTRY:
                        result = {"ok": False, "error": "herramienta desconocida"}
                    else:
                        try:
                            result = TOOL_REGISTRY[name](**arguments)
                        except TypeError as exc:
                            result = {"ok": False, "error": str(exc)}

                tool_calls_summary.append(
                    {"name": name, "arguments": arguments, "result": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        else:
            final_response = "ERROR: el agente excedió el máximo de iteraciones."
            error = final_response
    except Exception as exc:
        error = str(exc)
        final_response = ""

    return {
        "final_response": final_response,
        "tool_calls": tool_calls_summary,
        "messages": messages,
        "error": error,
    }
