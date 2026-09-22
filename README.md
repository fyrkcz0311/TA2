# UTP Assistant

Aplicación Streamlit para que UTPConsult procese correos de clientes mediante function calling.
Usa un modelo compatible con la API de OpenAI (DeepSeek por defecto) y servicios locales simulados.
La interfaz conserva la respuesta interna y la traza completa de cada ejecución.
El aspecto se define en `.streamlit/config.toml` (paleta y tipografías del tema) y en `styles.css`, que `app.py` inyecta al arrancar.

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
python -m unittest discover -v          # pruebas locales, sin red ni API key
python test_cli.py                      # los 3 correos contra el proveedor real
python test_cli.py 03_inyeccion.txt     # un correo concreto
```

Las suites `test_utp_assistant.py` y `test_regressions.py` usan clientes LLM simulados, así que no consumen créditos. Cubren el bucle de herramientas, la configuración, los schemas, las validaciones, el aislamiento entre sesiones y la interfaz Streamlit.

## Estructura

```text
utp_assistant/
├── .streamlit/config.toml
├── app.py
├── styles.css
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

El riesgo más grave del sistema es que el modelo redacte "ticket creado" sin haber invocado la herramienta. `agent.detect_inconsistencies()` compara la sección ACCIONES EJECUTADAS contra los identificadores realmente devueltos por los servicios. Detecta:

1. El resumen declara acciones pero no se invocó ninguna herramienta con éxito.
2. El resumen cita identificadores que ningún servicio devolvió.
3. Una línea carece de identificador válido, o menciona un tipo de acción (ticket, reunión, contacto) sin un identificador de esa herramienta.
4. Falta la sección, está vacía o declara "Ninguna" pese a existir ejecuciones exitosas.

Ante una inconsistencia, el agente devuelve el fallo al modelo una vez para que se corrija. Si insiste, la incidencia se expone como advertencia en la interfaz. La comprobación textual es conservadora y no equivale a verificar semánticamente cualquier redacción posible.

## Traza de auditoría

Cada invocación queda registrada con sus argumentos, resultado y marca de tiempo. Streamlit conserva un `ServiceState` por sesión: limpiar una sesión no afecta a las demás. Los argumentos y resultados se copian al registrar y al consultar, para impedir modificaciones accidentales del historial.

`run_agent()` devuelve la auditoría en `result["audit_log"]`. Para acumular varias ejecuciones, pasa el mismo `service_state=mock_services.ServiceState()` y consulta `mock_services.get_execution_log(state)`. Sin estado explícito, cada llamada al agente está aislada.

Las reuniones se validan contra la hora real actual y el comienzo de la fecha simulada, tomando el mayor de ambos. El día de la semana se calcula en America/Lima incluso cuando se recibe otro offset. Los servicios validan localmente los tipos de todos los argumentos; el email del CRM puede estar vacío si se desconoce.
