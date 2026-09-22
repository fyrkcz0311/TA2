"""Interfaz Streamlit para procesar correos con UTP Assistant."""

from __future__ import annotations

from datetime import datetime
from html import escape
import os
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

import mock_services
from agent import DEFAULT_BASE_URL, DEFAULT_MODEL, run_agent


BASE_DIR = Path(__file__).parent
EMAILS_DIR = BASE_DIR / "emails_prueba"
STYLESHEET = BASE_DIR / "styles.css"


def _read_test_email(filename: str) -> str:
    return (EMAILS_DIR / filename).read_text(encoding="utf-8")


def _load_selected_email() -> None:
    st.session_state["email_text"] = _read_test_email(st.session_state["selected_email"])


def _apply_styles() -> None:
    st.markdown(f"<style>{STYLESHEET.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)


def _html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def _readout(rows: list[tuple[str, str, bool]], stacked: bool = False) -> str:
    """Lista de etiqueta y valor; el tercer campo marca los valores que piden atención."""
    filas = "".join(
        f'<div class="readout-row{" is-alert" if alerta else ""}">'
        f"<span>{escape(etiqueta)}</span><b>{escape(valor)}</b></div>"
        for etiqueta, valor, alerta in rows
    )
    return f'<div class="readout{" is-stacked" if stacked else ""}">{filas}</div>'


def _tool_row(posicion: int, call: dict) -> str:
    resultado = call["result"]
    correcta = resultado.get("ok") is True
    identificador = resultado.get("id", "") if correcta else resultado.get("error", "sin detalle")
    return (
        '<div class="tool-row">'
        f'<span class="n">{posicion}</span>'
        f'<span class="chip {"chip-ok" if correcta else "chip-fail"}">'
        f'{"ejecutada" if correcta else "rechazada"}</span>'
        f'<span class="name">{escape(call["name"])}</span>'
        f'<span class="id{"" if correcta else " is-error"}">{escape(str(identificador))}</span>'
        "</div>"
    )


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


load_dotenv()
st.set_page_config(page_title="UTP Assistant", page_icon="✉", layout="wide")
_apply_styles()

if "runs" not in st.session_state:
    st.session_state["runs"] = []
if "service_state" not in st.session_state:
    st.session_state["service_state"] = mock_services.ServiceState()
if "selected_email" not in st.session_state:
    st.session_state["selected_email"] = "01_techcorp.txt"
if "email_text" not in st.session_state:
    st.session_state["email_text"] = _read_test_email("01_techcorp.txt")

with st.sidebar:
    simulated_date = st.date_input(
        "Fecha actual simulada",
        value=datetime.now(mock_services.LIMA_TZ).date(),
        min_value=datetime.now(mock_services.LIMA_TZ).date(),
        help="Solo permite adelantar el reloj. Retroceder no habilitaría agendar "
        "en el pasado: los servicios validan también contra la fecha real.",
    )
    _html('<h2 class="card-title">Proveedor LLM</h2>')
    _html(
        _readout(
            [
                ("Modelo", os.getenv("LLM_MODEL") or DEFAULT_MODEL, False),
                ("URL base", os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL, False),
                ("Schemas strict", os.getenv("LLM_STRICT_TOOLS") or "true", False),
            ],
            stacked=True,
        )
    )
    st.write("")
    if st.button("Limpiar historial"):
        st.session_state["runs"] = []
        mock_services.reset_log(st.session_state["service_state"])

_html(
    '<header class="masthead">'
    '<h1 class="masthead-title">UTP Assistant</h1>'
    "<p>Pega el correo de un cliente de UTP Consult. El agente decide qué hacer, "
    "ejecuta las acciones en Jira, Calendar y CRM, y deja registrado lo que realmente "
    "llegó a ejecutarse.</p>"
    "</header>"
)

runs = st.session_state["runs"]
audit_log = mock_services.get_execution_log(st.session_state["service_state"])
ejecutadas = sum(1 for entrada in audit_log if entrada["result"].get("ok") is True)
advertencias = sum(len(run["warnings"]) for run in runs)

composer, panel = st.columns([3, 2], gap="large")

with composer:
    _html('<h2 class="section-title">Correo entrante</h2>')
    test_emails = sorted(path.name for path in EMAILS_DIR.glob("*.txt"))
    st.selectbox(
        "Cargar correo de prueba",
        options=test_emails,
        key="selected_email",
        on_change=_load_selected_email,
    )
    email_text = st.text_area("Texto del correo", height=240, key="email_text")

    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key:
        st.error("Falta LLM_API_KEY. Copia .env.example como .env y completa la clave para procesar correos.")

    procesar = st.button("Procesar correo", type="primary", disabled=not bool(api_key))

with panel:
    with st.container(border=True):
        _html('<span class="card-anchor"></span><h2 class="section-title">Estado de la sesión</h2>')
        _html(
            _readout(
                [
                    ("Correos procesados", str(len(runs)), False),
                    ("Llamadas registradas", str(len(audit_log)), False),
                    ("Acciones completadas", str(ejecutadas), False),
                    ("Advertencias", str(advertencias), advertencias > 0),
                    ("Fecha simulada", simulated_date.isoformat(), False),
                ]
            )
        )
        st.caption(
            "Cada sesión del navegador tiene su propia traza: limpiar el historial aquí "
            "no borra el de nadie más."
        )

if procesar:
    with st.spinner("Analizando correo y ejecutando acciones..."):
        result = run_agent(email_text, simulated_date.isoformat(), service_state=st.session_state["service_state"])
    result["procesado_a"] = datetime.now(mock_services.LIMA_TZ).strftime("%H:%M:%S")
    st.session_state["runs"].insert(0, result)
    st.rerun()

st.write("")

if not runs:
    _html(
        '<div class="empty">Todavía no has procesado ningún correo. '
        "Carga uno de prueba o pega el tuyo y pulsa <b>Procesar correo</b>: "
        "aquí aparecerá la respuesta para el equipo interno junto a las herramientas que se invocaron.</div>"
    )

for posicion, run in enumerate(runs):
    numero = len(runs) - posicion
    correctas = sum(1 for call in run["tool_calls"] if call["result"].get("ok") is True)
    with st.container(border=True):
        cabecera = [
            '<span class="card-anchor"></span>',
            '<div class="run-head">',
            f'<span class="run-n">Ejecución {numero}</span>',
            f'<span class="meta">{escape(run.get("procesado_a", "--:--:--"))}</span>',
            f'<span class="meta">{_plural(len(run["tool_calls"]), "herramienta", "herramientas")}</span>',
        ]
        if correctas:
            cabecera.append(f'<span class="chip chip-ok">{_plural(correctas, "acción", "acciones")}</span>')
        if run["warnings"]:
            cabecera.append(
                f'<span class="chip chip-warn">{_plural(len(run["warnings"]), "advertencia", "advertencias")}</span>'
            )
        if run["error"] is not None:
            cabecera.append('<span class="chip chip-fail">error</span>')
        cabecera.append("</div>")
        _html("".join(cabecera))

        if run["error"] is not None:
            st.error(run["error"])
        for warning in run["warnings"]:
            st.warning(f"Verificación automática: {warning}")

        _html('<h3 class="card-title">Respuesta al equipo interno</h3>')
        st.code(run["final_response"] or "(sin respuesta)", language=None)

        with st.expander(f"Herramientas invocadas ({len(run['tool_calls'])})"):
            if not run["tool_calls"]:
                st.write("No se invocaron herramientas.")
            for indice, call in enumerate(run["tool_calls"], start=1):
                _html(_tool_row(indice, call))
                argumentos, resultado = st.columns(2, gap="medium")
                with argumentos:
                    st.caption("Argumentos")
                    st.json(call["arguments"])
                with resultado:
                    st.caption("Resultado")
                    st.json(call["result"])

        with st.expander("Historial de mensajes crudo"):
            st.json(run["messages"])

st.divider()
with st.expander(f"Traza de auditoría de la sesión ({len(audit_log)} llamadas)"):
    if not audit_log:
        st.write("Todavía no se ha ejecutado ninguna herramienta.")
    else:
        st.json(audit_log)
