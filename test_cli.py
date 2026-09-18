"""Ejecuta el caso TechCorp sin necesidad de la interfaz Streamlit."""

from datetime import date
import json
from pathlib import Path

from agent import run_agent


def main() -> None:
    email_path = Path(__file__).parent / "emails_prueba" / "01_techcorp.txt"
    result = run_agent(email_path.read_text(encoding="utf-8"), date.today().isoformat())

    print("RESPUESTA FINAL")
    print(result["final_response"])
    print("\nHERRAMIENTAS INVOCADAS")
    if result["tool_calls"]:
        for call in result["tool_calls"]:
            print(f"\n{call['name']}")
            print("Argumentos:")
            print(json.dumps(call["arguments"], indent=2, ensure_ascii=False))
            print("Resultado:")
            print(json.dumps(call["result"], indent=2, ensure_ascii=False))
    else:
        print("Ninguna")

    if result["error"]:
        print("\nERROR")
        print(result["error"])


if __name__ == "__main__":
    main()
