"""Pruebas de regresión para el agente, los schemas y los servicios simulados."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from types import SimpleNamespace
import unittest

import agent
import mock_services
import safety
import tools


def _dia_habil(base: date) -> date:
    """Primer día laborable a partir de `base`, para no chocar con la regla de fin de semana."""
    while base.weekday() >= 5:
        base += timedelta(days=1)
    return base


# Las fechas se calculan a partir de hoy para que la suite no caduque con el tiempo.
HOY = date.today()
REFERENCIA_FUTURA = _dia_habil(HOY + timedelta(days=365))
REFERENCIA = REFERENCIA_FUTURA.isoformat()
REFERENCIA_PASADA = _dia_habil(HOY - timedelta(days=365))


class _FakeResponse:
    def __init__(self, content, raw_calls) -> None:
        self.output = [
            {"type": "function_call", "call_id": f"call_{index}",
             "name": name, "arguments": arguments}
            for index, (name, arguments) in enumerate(raw_calls)
        ]
        if content is not None:
            self.output.append({"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": content}]})

    def model_dump(self):
        return {"output": self.output}


class FakeClient:
    """Reproduce una lista de turnos del LLM sin tocar la red.

    Cada turno es (estado_ignorado, content, [(nombre_herramienta, argumentos_json)]).
    Si se agotan los turnos se repite el último, para poder probar bucles largos.
    """

    def __init__(self, turns) -> None:
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(deepcopy(kwargs))
        turn = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        _, content, raw_calls = turn
        return _FakeResponse(content, raw_calls)


CRM_ARGS = json.dumps(
    {
        "contact_name": "Ana Torres",
        "company_name": "TechCorp",
        "email": "ana.torres@techcorp.com",
        "lead_status": "calificado",
        "notes": "Interes en avanzar.",
    }
)


class ConfiguracionTest(unittest.TestCase):
    def test_falla_con_mensaje_explicito_si_no_hay_api_key(self):
        with self.assertRaises(agent.ConfigError) as ctx:
            agent.resolve_config({"LLM_MODEL": "deepseek-chat"})
        self.assertIn("LLM_API_KEY", str(ctx.exception))

    def test_usa_modelo_y_url_por_defecto_cuando_faltan(self):
        config = agent.resolve_config({"LLM_API_KEY": "sk-test"})
        self.assertEqual(config.model, agent.DEFAULT_MODEL)
        self.assertEqual(config.base_url, agent.DEFAULT_BASE_URL)

    def test_respeta_modelo_configurado(self):
        config = agent.resolve_config({"LLM_API_KEY": "sk-test", "LLM_MODEL": "gpt-4o"})
        self.assertEqual(config.model, "gpt-4o")


class RedaccionTest(unittest.TestCase):
    def test_oculta_secretos_comunes_y_es_idempotente(self):
        source = "Contraseña: abc123. API_KEY=sk-test123456789. Tarjeta 4111 1111 1111 1111."
        safe, secrets = safety.sanitize_email(source)
        self.assertNotIn("abc123", safe)
        self.assertNotIn("sk-test", safe)
        self.assertNotIn("4111", safe)
        self.assertEqual(safety.redact_text(safe, secrets), safe)


class SchemaHerramientasTest(unittest.TestCase):
    def test_todas_las_herramientas_declaran_strict(self):
        for tool in tools.get_tools():
            self.assertTrue(tool["function"]["strict"], tool["function"]["name"])

    def test_strict_desactivable_para_proveedores_que_no_lo_soportan(self):
        for tool in tools.get_tools(strict=False):
            self.assertNotIn("strict", tool["function"])

    def test_strict_exige_que_todo_parametro_sea_requerido(self):
        for tool in tools.get_tools():
            parameters = tool["function"]["parameters"]
            self.assertEqual(
                sorted(parameters["properties"]), sorted(parameters["required"])
            )
            self.assertFalse(parameters["additionalProperties"])

    def test_get_tools_no_muta_la_definicion_original(self):
        tools.get_tools(strict=False)
        self.assertTrue(all("strict" in t["function"] for t in tools.get_tools()))

    def test_responses_conserva_los_tres_contratos(self):
        response_tools = tools.get_response_tools()
        self.assertEqual(len(response_tools), 3)
        for response_tool, original in zip(response_tools, tools.get_tools()):
            self.assertEqual(response_tool["name"], original["function"]["name"])
            self.assertEqual(response_tool["parameters"], original["function"]["parameters"])
            self.assertTrue(response_tool["strict"])


class ServiciosSimuladosTest(unittest.TestCase):
    def setUp(self):
        mock_services.reset_log()
        mock_services.set_reference_date(REFERENCIA)

    def test_rechaza_reunion_en_fecha_pasada(self):
        pasado = _dia_habil(HOY - timedelta(days=30))
        result = mock_services.agendar_reunion_en_google_calendar(
            "Reunion retroactiva",
            ["ana.torres@techcorp.com", "proyectos@utpconsult.com"],
            f"{pasado.isoformat()}T10:00:00-05:00",
            45,
            "nota",
        )
        self.assertFalse(result["ok"])
        self.assertIn("pasado", result["error"])

    def test_una_fecha_simulada_pasada_no_habilita_agendar_en_el_pasado(self):
        """La fecha simulada no debe poder usarse para viajar al pasado real."""
        mock_services.set_reference_date(REFERENCIA_PASADA.isoformat())
        siguiente = _dia_habil(REFERENCIA_PASADA + timedelta(days=1))
        result = mock_services.agendar_reunion_en_google_calendar(
            "Reunion en 2020",
            ["ana.torres@techcorp.com"],
            f"{siguiente.isoformat()}T10:00:00-05:00",
            60,
            "nota",
        )
        self.assertFalse(result["ok"], result)
        self.assertIn("pasado", result["error"])

    def test_una_fecha_simulada_futura_si_desplaza_el_minimo(self):
        """Simular el futuro debe seguir permitiendo agendar en ese futuro."""
        mock_services.set_reference_date(REFERENCIA)
        result = mock_services.agendar_reunion_en_google_calendar(
            "Reunion futura",
            ["ana.torres@techcorp.com"],
            f"{REFERENCIA}T10:00:00-05:00",
            60,
            "nota",
        )
        self.assertTrue(result["ok"], result)

    def test_acepta_reunion_el_mismo_dia_de_referencia(self):
        result = mock_services.agendar_reunion_en_google_calendar(
            "Reunion de hoy",
            ["ana.torres@techcorp.com"],
            f"{REFERENCIA}T10:00:00-05:00",
            45,
            "nota",
        )
        self.assertTrue(result["ok"], result)

    def test_rechaza_reunion_en_fin_de_semana(self):
        sabado = REFERENCIA_FUTURA + timedelta(days=(5 - REFERENCIA_FUTURA.weekday()) % 7)
        result = mock_services.agendar_reunion_en_google_calendar(
            "Reunion sabado",
            ["ana.torres@techcorp.com"],
            f"{sabado.isoformat()}T10:00:00-05:00",
            45,
            "nota",
        )
        self.assertFalse(result["ok"])
        self.assertIn("fin de semana", result["error"])

    def test_rechaza_start_time_sin_zona_horaria(self):
        result = mock_services.agendar_reunion_en_google_calendar(
            "Reunion sin zona",
            ["ana.torres@techcorp.com"],
            f"{REFERENCIA}T10:00:00",
            45,
            "nota",
        )
        self.assertFalse(result["ok"])
        self.assertIn("zona horaria", result["error"])

    def test_rechaza_summary_de_mas_de_80_caracteres(self):
        result = mock_services.crear_ticket_en_jira(
            "VENTAS", "x" * 81, "detalle", "Medium", "Task"
        )
        self.assertFalse(result["ok"])

    def test_rechaza_lead_status_invalido(self):
        result = mock_services.actualizar_contacto_en_crm(
            "Ana Torres", "TechCorp", "a@t.com", "inexistente", "nota"
        )
        self.assertFalse(result["ok"])

    def test_crea_ticket_valido_con_identificador(self):
        result = mock_services.crear_ticket_en_jira(
            "VENTAS", "[TechCorp] Analizar requisitos", "detalle", "Medium", "Story"
        )
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["id"].startswith("VENTAS-"))

    def test_expone_la_traza_de_auditoria(self):
        mock_services.crear_ticket_en_jira(
            "VENTAS", "[TechCorp] Requisitos", "detalle", "Medium", "Story"
        )
        log = mock_services.get_execution_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["tool"], "crear_ticket_en_jira")

    def test_la_traza_devuelta_no_permite_mutar_el_estado_interno(self):
        mock_services.crear_ticket_en_jira(
            "VENTAS", "[TechCorp] Requisitos", "detalle", "Medium", "Story"
        )
        mock_services.get_execution_log().clear()
        self.assertEqual(len(mock_services.get_execution_log()), 1)


class BucleDelAgenteTest(unittest.TestCase):
    def setUp(self):
        mock_services.reset_log()
        mock_services.set_reference_date(REFERENCIA)

    def test_ejecuta_herramientas_en_responses(self):
        client = FakeClient(
            [
                ("stop", None, [("actualizar_contacto_en_crm", CRM_ARGS)]),
                ("stop", "RESUMEN: listo", []),
            ]
        )
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertTrue(result["tool_calls"][0]["result"]["ok"], result)
        self.assertEqual(result["final_response"], "RESUMEN: listo")
        self.assertIsNone(result["error"])

    def test_ejecuta_varias_herramientas_en_un_response(self):
        client = FakeClient(
            [
                ("tool_calls", None, [("actualizar_contacto_en_crm", CRM_ARGS),
                                      ("crear_ticket_en_jira", TICKET_ARGS)]),
                ("stop", "RESUMEN: listo", []),
            ]
        )
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertEqual(len(result["tool_calls"]), 2)
        self.assertEqual(result["final_response"], "RESUMEN: listo")

    def test_devuelve_error_si_se_exceden_las_iteraciones(self):
        client = FakeClient(
            [("tool_calls", None, [("actualizar_contacto_en_crm", CRM_ARGS)])]
        )
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertIsNotNone(result["error"])
        self.assertIn("iteraciones", result["error"])

    def test_reporta_error_cuando_el_modelo_no_devuelve_respuesta_final(self):
        client = FakeClient([("stop", None, [])])
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertIsNotNone(result["error"])

    def test_informa_error_legible_si_el_proveedor_falla(self):
        class ClienteQueFalla:
            def __init__(self):
                self.responses = SimpleNamespace(create=self._boom)

            def _boom(self, **kwargs):
                raise RuntimeError("401 Unauthorized")

        result = agent.run_agent("correo", REFERENCIA, client=ClienteQueFalla())
        self.assertIn("401 Unauthorized", result["error"])
        self.assertEqual(result["final_response"], "")

    def test_devuelve_error_a_la_herramienta_si_el_modelo_la_inventa(self):
        client = FakeClient(
            [
                ("tool_calls", None, [("borrar_base_de_datos", "{}")]),
                ("stop", "RESUMEN: sin acciones", []),
            ]
        )
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertFalse(result["tool_calls"][0]["result"]["ok"])
        self.assertEqual(result["final_response"], "RESUMEN: sin acciones")

    def test_devuelve_error_a_la_herramienta_si_los_argumentos_no_son_json(self):
        client = FakeClient(
            [
                ("tool_calls", None, [("actualizar_contacto_en_crm", "{no es json")]),
                ("stop", "RESUMEN: sin acciones", []),
            ]
        )
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertFalse(result["tool_calls"][0]["result"]["ok"])

    def test_envia_la_fecha_de_referencia_en_el_system_prompt(self):
        client = FakeClient([("stop", "RESUMEN: listo", [])])
        agent.run_agent("correo", REFERENCIA, client=client)
        self.assertIn(REFERENCIA, client.requests[0]["instructions"])

    def test_devuelve_resultados_de_funciones_al_proveedor(self):
        client = FakeClient(
            [
                ("tool_calls", None, [("actualizar_contacto_en_crm", CRM_ARGS)]),
                ("stop", "RESUMEN: listo", []),
            ]
        )
        agent.run_agent("correo", REFERENCIA, client=client)
        second_input = client.requests[1]["input"]
        self.assertTrue(any(item.get("type") == "function_call" for item in second_input))
        self.assertTrue(any(item.get("type") == "function_call_output" for item in second_input))

    def test_requisito_explicito_omitido_exige_correccion(self):
        client = FakeClient([
            ("stop", RESUMEN_SIN_ACCIONES, []),
            ("tool_calls", None, [("crear_ticket_en_jira", TICKET_ARGS)]),
            ("stop", "RESUMEN: listo\nACCIONES EJECUTADAS: Ticket VENTAS-101 creado.\nPENDIENTES: Ninguno", []),
        ])
        result = agent.run_agent(
            "De: ana@techcorp.com\nNecesitamos que el portal permita descargar reportes mensuales.",
            REFERENCIA, client=client,
        )
        self.assertEqual(len(client.requests), 3)
        self.assertTrue(result["tool_calls"][0]["result"]["ok"])
        self.assertEqual(result["warnings"], [])

    def test_contacto_identificado_y_requisito_omitidos_se_detectan(self):
        email = ("De: lucia.ramos@innovatech.com\n"
                 "Somos Lucía Ramos de InnovaTech. Necesitamos que el portal permita descargar reportes.")
        problems = agent.detect_missing_actions(email, [])
        self.assertEqual(len(problems), 2)
        self.assertIn("ticket", problems[0])
        self.assertIn("CRM", problems[1])

    def test_no_exige_ticket_sin_datos_para_identificar_al_cliente(self):
        self.assertEqual(
            agent.detect_missing_actions("Necesitamos que el portal permita descargar reportes.", []),
            [],
        )

    def test_secretos_no_llegan_al_modelo_ni_a_la_traza(self):
        crm_with_secret = json.loads(CRM_ARGS)
        crm_with_secret["notes"] = "La contraseña es abc123"
        client = FakeClient([
            ("tool_calls", None, [("actualizar_contacto_en_crm", json.dumps(crm_with_secret))]),
            ("stop", "RESUMEN: La contraseña es abc123.\nACCIONES EJECUTADAS: Contacto crm_ac8192b6 actualizado.\nPENDIENTES: Ninguno", []),
        ])
        result = agent.run_agent("Mi contraseña es abc123. Necesito información.", REFERENCIA, client=client)
        self.assertIsNone(result["error"])
        for value in (client.requests, result):
            self.assertNotIn("abc123", json.dumps(value, ensure_ascii=False))
        self.assertIn("[DATO SENSIBLE OMITIDO]", json.dumps(result, ensure_ascii=False))


RESUMEN_ALUCINADO = """RESUMEN: Lucia Ramos, de InnovaTech, solicita el portal de clientes.

ACCIONES EJECUTADAS:
- Ticket creado en Jira (proyecto VENTAS): "[InnovaTech] Requisitos portal de clientes".
- Contacto actualizado en CRM: Lucia Ramos (InnovaTech), lead_status "calificado".

PENDIENTES:
- No se agendo reunion: falta dia y hora.

SIGUIENTE PASO SUGERIDO: Contactar a Lucia Ramos para confirmar la reunion."""

RESUMEN_SIN_ACCIONES = """RESUMEN: Lucia Ramos escribe pidiendo informacion.

ACCIONES EJECUTADAS: Ninguna

PENDIENTES:
- Falta concretar dia y hora.

SIGUIENTE PASO SUGERIDO: Responder al cliente."""

TICKET_ARGS = json.dumps(
    {
        "project_key": "VENTAS",
        "summary": "[InnovaTech] Requisitos portal de clientes",
        "description": "- Reportes mensuales.",
        "priority": "Medium",
        "issue_type": "Story",
    }
)


class DeteccionDeAlucinacionesTest(unittest.TestCase):
    def test_detecta_acciones_declaradas_sin_herramientas_invocadas(self):
        problemas = agent.detect_inconsistencies(RESUMEN_ALUCINADO, [])
        self.assertTrue(problemas)
        self.assertIn("no se invocó ninguna herramienta", problemas[0])

    def test_no_marca_un_resumen_que_declara_ninguna_accion(self):
        self.assertEqual(agent.detect_inconsistencies(RESUMEN_SIN_ACCIONES, []), [])

    def test_detecta_identificadores_inventados(self):
        ejecutadas = [{"name": "crear_ticket_en_jira", "arguments": {}, "result": {"ok": True, "id": "VENTAS-101"}}]
        resumen = "RESUMEN: x\n\nACCIONES EJECUTADAS:\n- Ticket VENTAS-999 creado.\n\nPENDIENTES: Ninguno"
        problemas = agent.detect_inconsistencies(resumen, ejecutadas)
        self.assertTrue(problemas)
        self.assertIn("VENTAS-999", problemas[0])

    def test_no_marca_un_resumen_consistente(self):
        ejecutadas = [{"name": "crear_ticket_en_jira", "arguments": {}, "result": {"ok": True, "id": "VENTAS-101"}}]
        resumen = "RESUMEN: x\n\nACCIONES EJECUTADAS:\n- Ticket VENTAS-101 creado.\n\nPENDIENTES: Ninguno"
        self.assertEqual(agent.detect_inconsistencies(resumen, ejecutadas), [])

    def test_advierte_respuestas_sin_la_seccion_esperada(self):
        self.assertTrue(agent.detect_inconsistencies("texto libre sin secciones", []))


class ReparacionDeAlucinacionesTest(unittest.TestCase):
    def setUp(self):
        mock_services.reset_log()
        mock_services.set_reference_date(REFERENCIA)

    def test_pide_correccion_y_el_modelo_ejecuta_la_herramienta(self):
        client = FakeClient(
            [
                ("stop", RESUMEN_ALUCINADO, []),
                ("stop", None, [("crear_ticket_en_jira", TICKET_ARGS)]),
                ("stop", "RESUMEN: ok\n\nACCIONES EJECUTADAS:\n- Ticket VENTAS-101.\n\nPENDIENTES: Ninguno", []),
            ]
        )
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertEqual(len(result["tool_calls"]), 1)
        self.assertEqual(result["warnings"], [])

    def test_advierte_si_el_modelo_insiste_en_la_alucinacion(self):
        client = FakeClient([("stop", RESUMEN_ALUCINADO, []), ("stop", RESUMEN_ALUCINADO, [])])
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertEqual(result["tool_calls"], [])
        self.assertTrue(result["warnings"])
        self.assertEqual(result["final_response"], RESUMEN_ALUCINADO)

    def test_solo_reintenta_una_vez(self):
        client = FakeClient([("stop", RESUMEN_ALUCINADO, []), ("stop", RESUMEN_ALUCINADO, [])])
        agent.run_agent("correo", REFERENCIA, client=client)
        self.assertEqual(len(client.requests), 2)

    def test_una_corrida_consistente_no_genera_advertencias(self):
        client = FakeClient([("stop", RESUMEN_SIN_ACCIONES, [])])
        result = agent.run_agent("correo", REFERENCIA, client=client)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(len(client.requests), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
