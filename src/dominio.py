"""Reglas de negocio reutilizables por API y consumidores de eventos."""
from decimal import Decimal, ROUND_HALF_UP


def salud(avance_pct, en_riesgo, estado):
    if en_riesgo:
        return "rojo"
    if estado == "en_curso" and Decimal(str(avance_pct)) < 50:
        return "ambar"
    return "verde"


def calcular_score(valor_estrategico, urgencia, riesgo, costo, beneficio):
    costo, beneficio = Decimal(str(costo)), Decimal(str(beneficio))
    retorno = min(beneficio / costo, Decimal("3")) if costo > 0 else Decimal("3")
    bruto = (Decimal(valor_estrategico) * 8 + Decimal(urgencia) * 5
             + retorno * Decimal("8.33") - Decimal(riesgo) * 2)
    return min(Decimal("100"), max(Decimal("0"), bruto)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
