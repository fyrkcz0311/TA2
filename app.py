"""Interfaz Streamlit para procesar correos con UTP Assistant."""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

from agent import run_agent
from mock_services import reset_log


BASE_DIR = Path(__file__).parent
EMAILS_DIR = BASE_DIR / "emails_prueba"


def _read_test_email(filename: str) -> str:
    return (EMAILS_DIR / filename).read_text(encoding="utf-8")


def _load_selected_email() -> None:
    st.session_state["email_text"] = _read_test_email(st.session_state["selected_email"])


load_dotenv()
st.set_page_config(page_title="UTP Assistant", layout="wide")
st.title("UTP Assistant - Procesador de correos")

if "runs" not in st.session_state:
    st.session_state["runs"] = []
if "selected_email" not in st.session_state:
    st.session_state["selected_email"] = "01_techcorp.txt"
if "email_text" not in st.session_state:
    st.session_state["email_text"] = _read_test_email("01_techcorp.txt")

with st.sidebar:
    simulated_date = st.date_input("Fecha actual simulada", value=date.today())
    st.caption(f"Modelo: {os.getenv('LLM_MODEL', 'No configurado')}")
    st.caption(f"URL base: {os.getenv('LLM_BASE_URL', 'No configurada')}")
    if st.button("Limpiar historial"):
        st.session_state["runs"] = []
        reset_log()

test_emails = sorted(path.name for path in EMAILS_DIR.glob("*.txt"))
st.selectbox(
    "Cargar correo de prueba",
    options=test_emails,
    key="selected_email",
    on_change=_load_selected_email,
)
email_text = st.text_area("Correo entrante", height=220, key="email_text")

api_key = os.getenv("LLM_API_KEY", "")
if not api_key:
    st.error("Falta LLM_API_KEY. Copia .env.example como .env y completa la clave para procesar correos.")

if st.button("Procesar correo", disabled=not bool(api_key)):
    with st.spinner("Analizando correo y ejecutando acciones..."):
        result = run_agent(email_text, simulated_date.isoformat())
    st.session_state["runs"].insert(0, result)

for run in st.session_state["runs"]:
    st.subheader("Respuesta al equipo interno")
    st.code(run["final_response"], language=None)

    with st.expander(f"Herramientas invocadas ({len(run['tool_calls'])})"):
        if not run["tool_calls"]:
            st.write("No se invocaron herramientas.")
        for call in run["tool_calls"]:
            succeeded = call["result"].get("ok") is True
            prefix = "✅" if succeeded else "❌"
            st.write(f"{prefix} {call['name']}")
            st.write("Argumentos")
            st.json(call["arguments"])
            st.write("Resultado")
            st.json(call["result"])

    with st.expander("Historial de mensajes crudo"):
        st.json(run["messages"])

    if run["error"] is not None:
        st.error(run["error"])
