# UTP Assistant

Aplicación Streamlit para que UTPConsult procese correos de clientes mediante function calling.
Usa un modelo compatible con la API de OpenAI (DeepSeek por defecto) y servicios locales simulados.
La interfaz conserva la respuesta interna y la traza completa de cada ejecución.

## Requisitos

- Python 3.10 o superior.
- Una clave de API para el proveedor LLM compatible.

## Instalación

```bash
cd utp_assistant
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Completa `LLM_API_KEY` en `.env`. De forma predeterminada se usa `https://api.deepseek.com` y el modelo `deepseek-chat`; ambos valores pueden cambiarse en ese archivo.

## Ejecución

```bash
streamlit run app.py
python test_cli.py
```

`test_cli.py` procesa `emails_prueba/01_techcorp.txt`. La aplicación permite escoger cualquiera de los tres correos de prueba.

## Estructura

```text
utp_assistant/
├── app.py
├── agent.py
├── mock_services.py
├── tools.py
├── test_cli.py
├── emails_prueba/
├── requirements.txt
└── .env.example
```

## Herramientas

| Herramienta | Propósito |
| --- | --- |
| `crear_ticket_en_jira` | Registra requisitos, entregables o cambios de alcance. |
| `agendar_reunion_en_google_calendar` | Programa reuniones, llamadas o demostraciones solicitadas. |
| `actualizar_contacto_en_crm` | Crea o actualiza el contacto y su estado comercial. |
