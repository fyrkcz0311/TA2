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

`LLM_STRICT_TOOLS` controla si los schemas se envían con `"strict": true`, que obliga al proveedor a respetar enums, `additionalProperties` y `maxLength`. Si tu proveedor rechaza la petición por ese campo, ponlo en `false`; las validaciones de `mock_services.py` siguen actuando como segunda barrera.

## Ejecución

```bash
streamlit run app.py
```

## Pruebas

```bash
python -m unittest test_utp_assistant   # 25 pruebas, sin red ni API key
python test_cli.py                      # los 3 correos contra el proveedor real
python test_cli.py 03_inyeccion.txt     # un correo concreto
```

`test_utp_assistant.py` usa un cliente LLM simulado, así que no consume créditos. Cubre el bucle de herramientas, la resolución de configuración, los schemas y las validaciones de los servicios simulados.

## Estructura

```text
utp_assistant/
├── app.py
├── agent.py
├── mock_services.py
├── tools.py
├── test_cli.py
├── test_utp_assistant.py
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

Los tres schemas declaran `strict`, `additionalProperties: false` y todos sus parámetros como obligatorios. `mock_services.py` vuelve a validar cada argumento antes de simular el efecto, de modo que un argumento alucinado se rechaza con `{"ok": false, "error": ...}` y el modelo recibe ese error para corregirse.

## Verificación anti-alucinación

El riesgo más grave del sistema es que el modelo redacte "ticket creado" sin haber invocado la herramienta. `agent.detect_inconsistencies()` compara, de forma determinista y sin depender del modelo, lo que la sección ACCIONES EJECUTADAS afirma contra los identificadores realmente devueltos por los servicios. Detecta dos casos:

1. El resumen declara acciones pero no se invocó ninguna herramienta con éxito.
2. El resumen cita identificadores que ningún servicio devolvió.

Ante cualquiera de los dos, el agente devuelve el fallo al modelo una vez para que se corrija (invocando las herramientas o moviendo lo pendiente a PENDIENTES). Si insiste, la incidencia se expone como advertencia en la interfaz en lugar de silenciarse.

## Traza de auditoría

Cada invocación queda registrada con sus argumentos, su resultado y su marca de tiempo. La interfaz la muestra al pie bajo "Traza de auditoría de la sesión"; en código se consulta con `mock_services.get_execution_log()`.
