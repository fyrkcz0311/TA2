"""Regresiones de aislamiento, auditoría, fechas y validación local."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import agent
import mock_services as services
from test_utp_assistant import FakeClient, CRM_ARGS, REFERENCIA, RESUMEN_SIN_ACCIONES


class ServiceRegressionTest(unittest.TestCase):
    def setUp(self):
        self.state = services.ServiceState()
        self.context = services.service_session(self.state)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def ticket(self):
        return services.crear_ticket_en_jira('VENTAS', 'Original', 'Detalle', 'Medium', 'Task')

    def meeting(self, start):
        return services.agendar_reunion_en_google_calendar('Reunión', ['a@b.com'], start, 45, '')

    def test_audit_copy_and_tool_result_cannot_change_history(self):
        result = self.ticket()
        result['echo']['summary'] = 'Cambio externo'
        log = services.get_execution_log()
        log[0]['args']['summary'] = 'Cambio copia'
        log[0]['result']['ok'] = False
        saved = services.get_execution_log()[0]
        self.assertEqual(saved['args']['summary'], 'Original')
        self.assertEqual(saved['result']['echo']['summary'], 'Original')
        self.assertTrue(saved['result']['ok'])

    def test_past_hour_today_is_rejected_but_future_hour_is_allowed(self):
        with patch.object(services, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2030, 1, 7, 18, tzinfo=services.LIMA_TZ)
            services.set_reference_date('2030-01-07')
            self.assertFalse(self.meeting('2030-01-07T10:00:00-05:00')['ok'])
            self.assertTrue(self.meeting('2030-01-07T19:00:00-05:00')['ok'])

    def test_weekday_is_checked_in_lima(self):
        with patch.object(services, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2030, 1, 1, tzinfo=services.LIMA_TZ)
            # Lunes UTC, domingo en Lima.
            self.assertFalse(self.meeting('2030-01-07T01:00:00+00:00')['ok'])
            # Sábado UTC, viernes en Lima.
            self.assertTrue(self.meeting('2030-01-12T01:00:00+00:00')['ok'])

    def test_all_string_arguments_reject_non_strings(self):
        cases = [
            (services.crear_ticket_en_jira, dict(project_key='VENTAS', summary='Ticket', description='Detalle', priority='Medium', issue_type='Task')),
            (services.actualizar_contacto_en_crm, dict(contact_name='Ana Torres', company_name='Empresa', email='', lead_status='nuevo', notes='')),
            (services.agendar_reunion_en_google_calendar, dict(summary='Reunión', attendees=['a@b.com'], start_time=f'{REFERENCIA}T10:00:00-05:00', duration_minutes=45, notes='')),
        ]
        for function, valid in cases:
            for field, value in valid.items():
                if not isinstance(value, str):
                    continue
                for invalid in (None, [], {}, 17, True):
                    with self.subTest(tool=function.__name__, field=field, invalid=invalid):
                        result = function(**(valid | {field: invalid}))
                        self.assertFalse(result['ok'])

    def test_durations_and_emails_are_validated(self):
        for duration in (45.0, True, [], {}, '45'):
            with self.subTest(duration=duration):
                self.assertFalse(services.agendar_reunion_en_google_calendar('Reunión', ['a@b.com'], f'{REFERENCIA}T10:00:00-05:00', duration, '')['ok'])
        for email in ('@', 'a@', 'a@@b.com', 'a b@c.com'):
            with self.subTest(email=email):
                self.assertFalse(services.actualizar_contacto_en_crm('Ana Torres', 'Empresa', email, 'nuevo', '')['ok'])
                self.assertFalse(services.agendar_reunion_en_google_calendar('Reunión', [email], f'{REFERENCIA}T10:00:00-05:00', 45, '')['ok'])
        self.assertTrue(services.actualizar_contacto_en_crm('Ana Torres', 'Empresa', '', 'nuevo', '')['ok'])

    def test_concurrent_sessions_keep_dates_and_logs_separate(self):
        barrier = Barrier(2)

        def worker(reference):
            state = services.ServiceState()
            with services.service_session(state):
                services.set_reference_date(reference)
                barrier.wait(timeout=5)
                result = self.meeting('2099-01-05T10:00:00-05:00')
                return result['ok'], services.get_execution_log()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(worker, '2099-01-01')
            second = executor.submit(worker, '2099-02-01')
            a, b = first.result(), second.result()
        self.assertTrue(a[0])
        self.assertFalse(b[0])
        self.assertEqual(len(a[1]), 1)
        self.assertEqual(len(b[1]), 1)
        self.assertEqual(services.get_execution_log(), [])


class SummaryRegressionTest(unittest.TestCase):
    def setUp(self):
        self.calls = [{'name': 'actualizar_contacto_en_crm', 'result': {'ok': True, 'id': 'crm_12345678'}}]

    def test_partial_hallucinations_are_detected(self):
        for actions in (
            'Contacto crm_12345678 actualizado. Ticket creado en Jira.',
            'Contacto crm_12345678 actualizado.\nTicket creado.',
            'Ticket crm_12345678 creado.',
            'Contacto actualizado.',
            'Ninguna. Ticket creado.',
            'Ninguna',
            '',
        ):
            with self.subTest(actions=actions):
                self.assertTrue(agent.detect_inconsistencies('ACCIONES EJECUTADAS: ' + actions, self.calls))

    def test_valid_contact_is_not_flagged(self):
        self.assertEqual(agent.detect_inconsistencies('ACCIONES EJECUTADAS: Contacto crm_12345678 actualizado.', self.calls), [])

    def test_agent_requests_repair_for_partial_hallucination(self):
        client = FakeClient([
            ('stop', None, [('actualizar_contacto_en_crm', CRM_ARGS)]),
            ('stop', 'ACCIONES EJECUTADAS: Contacto actualizado y ticket creado.', []),
        ])
        result = agent.run_agent('Correo', REFERENCIA, client=client)
        self.assertIsNone(result['error'])
        self.assertTrue(result['warnings'])
        self.assertEqual(len(client.requests), 3)
        self.assertEqual(len(result['audit_log']), 1)


class InterfaceRegressionTest(unittest.TestCase):
    def test_sessions_do_not_share_or_clear_each_others_audit(self):
        path = str(Path(__file__).with_name('app.py'))
        with patch.dict('os.environ', {'LLM_API_KEY': 'test-local'}):
            first = AppTest.from_file(path).run()
            second = AppTest.from_file(path).run()
            state = first.session_state['service_state']
            with services.service_session(state):
                services.crear_ticket_en_jira('VENTAS', 'Solo sesion A', 'Detalle', 'Medium', 'Task')
            first.run()
            second.run()
            self.assertTrue(any('Solo sesion A' in str(j.value) for j in first.json))
            self.assertFalse(any('Solo sesion A' in str(j.value) for j in second.json))
            next(b for b in second.button if b.label == 'Limpiar historial').click().run()
            self.assertEqual(len(services.get_execution_log(state)), 1)
            # Procesar desde la interfaz conserva el mismo estado en los reruns.
            client = FakeClient([('stop', RESUMEN_SIN_ACCIONES, [])])
            with patch('agent.get_client', return_value=client):
                next(b for b in first.button if b.label == 'Procesar correo').click().run()
            self.assertEqual(len(first.session_state['runs']), 1)
            self.assertIsNone(first.session_state['runs'][0]['error'])
            self.assertEqual(len(services.get_execution_log(state)), 1)
            next(b for b in first.button if b.label == 'Limpiar historial').click().run()
            self.assertEqual(services.get_execution_log(state), [])
            self.assertEqual(len(first.exception), 0)
            self.assertEqual(len(second.exception), 0)


if __name__ == '__main__':
    unittest.main()
