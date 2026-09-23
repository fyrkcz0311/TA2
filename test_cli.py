"""Ejecuta los correos de prueba contra el proveedor real, sin la interfaz Streamlit."""

from datetime import datetime
import json
from pathlib import Path
import sys

from agent import ConfigError, resolve_config, run_agent
from mock_services import LIMA_TZ

EMAILS_DIR = Path(__file__).parent / "emails_prueba"


def procesar(email_path: Path, today_iso: str) -> bool:
    print("=" * 70)
    print(email_path.name)
    print("=" * 70)
    result = run_agent(email_path.read_text(encoding="utf-8"), today_iso)

    if result["error"]:
        print(f"ERROR: {result['error']}")
        return False

    for warning in result["warnings"]:
        print(f"ADVERTENCIA: {warning}")

    print("RESPUESTA FINAL")
    print(result["final_response"])
    print(f"\nHERRAMIENTAS INVOCADAS: {len(result['tool_calls'])}")
    for call in result["tool_calls"]:
        estado = "OK" if call["result"].get("ok") else "RECHAZADA"
        print(f"\n[{estado}] {call['name']}")
        print("Argumentos:")
        print(json.dumps(call["arguments"], indent=2, ensure_ascii=False))
        print("Resultado:")
        print(json.dumps(call["result"], indent=2, ensure_ascii=False))
    print()
    return not result["warnings"]


def main() -> int:
    try:
        config = resolve_config()
    except ConfigError as exc:
        print(f"{exc}\nEste script necesita el proveedor real; para las pruebas "
              f"sin red usa: python -m unittest test_utp_assistant")
        return 1

    print(f"Modelo: {config.model} ({config.base_url})\n")
    today_iso = datetime.now(LIMA_TZ).date().isoformat()

    seleccion = sys.argv[1:] or sorted(path.name for path in EMAILS_DIR.glob("*.txt"))
    fallos = [nombre for nombre in seleccion if not procesar(EMAILS_DIR / nombre, today_iso)]

    if fallos:
        print(f"Correos con error: {', '.join(fallos)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
