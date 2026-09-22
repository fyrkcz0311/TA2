"""Servicios locales deterministas que sustituyen Jira, Calendar y CRM."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any


VALID_PROJECT_KEYS = {"VENTAS", "PROY"}
VALID_PRIORITIES = {"Lowest", "Low", "Medium", "High", "Highest"}
VALID_ISSUE_TYPES = {"Task", "Story", "Bug", "Epic"}
VALID_DURATIONS = {30, 45, 60, 90}
VALID_LEAD_STATUSES = {
    "nuevo",
    "contactado",
    "calificado",
    "propuesta_enviada",
    "negociacion",
    "ganado",
    "perdido",
}

LIMA_TZ = timezone(timedelta(hours=-5))

@dataclass
class ServiceState:
    execution_log: list[dict[str, Any]] = field(default_factory=list)
    ticket_counter: int = 101
    reference_date: date | None = None


_state: ContextVar[ServiceState | None] = ContextVar("service_state", default=None)


def _current_state() -> ServiceState:
    state = _state.get()
    if state is None:
        state = ServiceState()
        _state.set(state)
    return state


@contextmanager
def service_session(state: ServiceState):
    """Activa el estado del llamador y restaura el contexto incluso si falla."""
    token = _state.set(state)
    try:
        yield state
    finally:
        _state.reset(token)


def set_reference_date(value: str | date | None) -> None:
    """Fija la fecha simulada "hoy" que la interfaz permite mover.

    Solo sirve para adelantar el reloj: ver `_earliest_allowed_start`.
    None vuelve al reloj real.
    """
    if value is None or isinstance(value, date):
        _current_state().reference_date = value
    else:
        _current_state().reference_date = date.fromisoformat(value)


def _earliest_allowed_start() -> datetime:
    """Momento más temprano en el que se puede agendar.

    Es el mayor entre la fecha simulada y la fecha real. Anclarlo solo a la
    fecha simulada permitiría anular la validación retrocediendo el reloj:
    con "hoy" en 2020 cualquier reunión de 2020 pasaría el control.
    """
    now = datetime.now(LIMA_TZ)
    reference = _current_state().reference_date
    if reference is None:
        return now
    return max(now, datetime.combine(reference, time.min, tzinfo=LIMA_TZ))


def reset_log(state: ServiceState | None = None) -> None:
    """Elimina la traza de ejecuciones registradas durante la sesión actual."""
    (state or _current_state()).execution_log.clear()


def get_execution_log(state: ServiceState | None = None) -> list[dict[str, Any]]:
    """Devuelve una copia de la traza de auditoría de todas las herramientas ejecutadas."""
    return deepcopy((state or _current_state()).execution_log)


def _log(tool: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    _current_state().execution_log.append(
        deepcopy({
            "tool": tool,
            "args": args,
            "result": result,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
    )
    return result


def _string_error(args: dict[str, Any], fields: tuple[str, ...]) -> str | None:
    for name in fields:
        if not isinstance(args[name], str):
            return f"{name} debe ser una cadena de texto"
    return None


def _valid_email(value: str) -> bool:
    return re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", value) is not None


def crear_ticket_en_jira(
    project_key: str,
    summary: str,
    description: str,
    priority: str,
    issue_type: str,
) -> dict[str, Any]:
    """Simula la creación de un ticket de Jira y valida su contrato."""

    args = {
        "project_key": project_key,
        "summary": summary,
        "description": description,
        "priority": priority,
        "issue_type": issue_type,
    }
    error = _string_error(args, tuple(args))
    if error:
        return _log("crear_ticket_en_jira", args, {"ok": False, "error": error})
    if project_key not in VALID_PROJECT_KEYS:
        return _log(
            "crear_ticket_en_jira",
            args,
            {"ok": False, "error": "project_key debe ser VENTAS o PROY"},
        )
    if priority not in VALID_PRIORITIES:
        return _log(
            "crear_ticket_en_jira",
            args,
            {"ok": False, "error": "priority no es válida"},
        )
    if issue_type not in VALID_ISSUE_TYPES:
        return _log(
            "crear_ticket_en_jira",
            args,
            {"ok": False, "error": "issue_type no es válido"},
        )
    if not isinstance(summary, str) or len(summary) > 80:
        return _log(
            "crear_ticket_en_jira",
            args,
            {"ok": False, "error": "summary debe tener como máximo 80 caracteres"},
        )

    state = _current_state()
    ticket_id = f"{project_key}-{state.ticket_counter}"
    state.ticket_counter += 1
    return _log(
        "crear_ticket_en_jira",
        args,
        {"ok": True, "id": ticket_id, "echo": args},
    )


def agendar_reunion_en_google_calendar(
    summary: str,
    attendees: list[str],
    start_time: str,
    duration_minutes: int,
    notes: str,
) -> dict[str, Any]:
    """Simula la creación de un evento con fechas y asistentes validados."""
    args = {
        "summary": summary,
        "attendees": attendees,
        "start_time": start_time,
        "duration_minutes": duration_minutes,
        "notes": notes,
    }
    error = _string_error(args, ("summary", "start_time", "notes"))
    if error:
        return _log("agendar_reunion_en_google_calendar", args, {"ok": False, "error": error})
    if not isinstance(start_time, str):
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "start_time debe ser una fecha ISO 8601 con zona horaria"},
        )
    try:
        scheduled_at = datetime.fromisoformat(start_time)
    except (TypeError, ValueError):
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "start_time no tiene un formato ISO 8601 válido"},
        )
    if scheduled_at.tzinfo is None:
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "start_time debe incluir zona horaria"},
        )
    if scheduled_at < _earliest_allowed_start():
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "no se pueden agendar reuniones en el pasado"},
        )
    if scheduled_at.astimezone(LIMA_TZ).weekday() >= 5:
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "no se pueden agendar reuniones en fin de semana"},
        )
    if type(duration_minutes) is not int or duration_minutes not in VALID_DURATIONS:
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "duration_minutes debe ser 30, 45, 60 o 90"},
        )
    if (
        not isinstance(attendees, list)
        or not attendees
        or not all(isinstance(attendee, str) and _valid_email(attendee) for attendee in attendees)
    ):
        return _log(
            "agendar_reunion_en_google_calendar",
            args,
            {"ok": False, "error": "attendees debe ser una lista no vacía de emails"},
        )

    event_digest = hashlib.md5(f"{summary}{start_time}".encode("utf-8")).hexdigest()[:8]
    return _log(
        "agendar_reunion_en_google_calendar",
        args,
        {"ok": True, "id": f"evt_{event_digest}", "echo": args},
    )


def actualizar_contacto_en_crm(
    contact_name: str,
    company_name: str,
    email: str,
    lead_status: str,
    notes: str,
) -> dict[str, Any]:
    """Simula una alta o actualización de contacto en el CRM."""
    args = {
        "contact_name": contact_name,
        "company_name": company_name,
        "email": email,
        "lead_status": lead_status,
        "notes": notes,
    }
    error = _string_error(args, tuple(args))
    if error:
        return _log("actualizar_contacto_en_crm", args, {"ok": False, "error": error})
    if email and not _valid_email(email):
        return _log("actualizar_contacto_en_crm", args, {"ok": False, "error": "email debe ser válido o vacío"})
    if lead_status not in VALID_LEAD_STATUSES:
        return _log(
            "actualizar_contacto_en_crm",
            args,
            {"ok": False, "error": "lead_status no es válido"},
        )
    if not isinstance(contact_name, str) or not contact_name.strip():
        return _log(
            "actualizar_contacto_en_crm",
            args,
            {"ok": False, "error": "contact_name no puede estar vacío"},
        )
    if not isinstance(company_name, str) or not company_name.strip():
        return _log(
            "actualizar_contacto_en_crm",
            args,
            {"ok": False, "error": "company_name no puede estar vacío"},
        )

    identity = email if isinstance(email, str) and email else contact_name
    contact_digest = hashlib.md5(identity.encode("utf-8")).hexdigest()[:8]
    return _log(
        "actualizar_contacto_en_crm",
        args,
        {"ok": True, "id": f"crm_{contact_digest}", "echo": args},
    )


TOOL_REGISTRY = {
    "crear_ticket_en_jira": crear_ticket_en_jira,
    "agendar_reunion_en_google_calendar": agendar_reunion_en_google_calendar,
    "actualizar_contacto_en_crm": actualizar_contacto_en_crm,
}


if __name__ == "__main__":
    _demo = datetime.now(LIMA_TZ).date() + timedelta(days=1)
    while _demo.weekday() >= 5:
        _demo += timedelta(days=1)
    _demo_iso = _demo.isoformat()

    print(
        crear_ticket_en_jira(
            "VENTAS",
            "[TechCorp] Analizar requisitos del módulo de pagos",
            "- Revisar requisitos iniciales.",
            "Medium",
            "Story",
        )
    )
    print(
        agendar_reunion_en_google_calendar(
            "Reunión técnica - TechCorp",
            ["ana.torres@techcorp.com", "proyectos@utpconsult.com"],
            f"{_demo_iso}T10:00:00-05:00",
            60,
            "FECHA TENTATIVA - confirmar con el cliente.",
        )
    )
    print(
        actualizar_contacto_en_crm(
            "Ana Torres",
            "TechCorp",
            "ana.torres@techcorp.com",
            "calificado",
            "Interés en avanzar.",
        )
    )
    print(crear_ticket_en_jira("VENTAS", "x" * 90, "Detalle", "Medium", "Task"))
    print(
        agendar_reunion_en_google_calendar(
            "Reunión sin zona",
            ["ana.torres@techcorp.com"],
            f"{_demo_iso}T10:00:00",
            45,
            "Sin zona horaria.",
        )
    )
    print(
        actualizar_contacto_en_crm(
            "Ana Torres",
            "TechCorp",
            "ana.torres@techcorp.com",
            "estado_inexistente",
            "Prueba.",
        )
    )
