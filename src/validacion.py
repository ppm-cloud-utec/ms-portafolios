"""Validación estricta en el límite HTTP: errores de usuario -> 400, nunca 502."""
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID


class ValidacionError(ValueError):
    pass


def objeto(valor, nombre="cuerpo"):
    if not isinstance(valor, dict):
        raise ValidacionError(f"{nombre} debe ser un objeto JSON")
    return valor


def campos_permitidos(c, permitidos):
    desconocidos = set(c) - set(permitidos)
    if desconocidos:
        raise ValidacionError(f"Campos no admitidos: {', '.join(sorted(desconocidos))}")


def texto(valor, nombre, *, maximo=200, obligatorio=True):
    if valor is None and not obligatorio:
        return None
    if not isinstance(valor, str):
        raise ValidacionError(f"{nombre} debe ser texto")
    limpio = valor.strip()
    if (obligatorio and not limpio) or len(limpio) > maximo or "\x00" in limpio:
        raise ValidacionError(f"{nombre} debe tener entre 1 y {maximo} caracteres")
    return limpio


def entero(valor, nombre, *, minimo=0, maximo=2_147_483_647):
    if isinstance(valor, bool) or not isinstance(valor, (str, int)):
        raise ValidacionError(f"{nombre} debe ser entero")
    s = str(valor)
    if not s.isascii() or not s.isdecimal():
        raise ValidacionError(f"{nombre} debe ser entero positivo")
    n = int(s) if len(s) <= 18 else maximo + 1
    if not minimo <= n <= maximo:
        raise ValidacionError(f"{nombre} debe estar entre {minimo} y {maximo}")
    return n


def dinero(valor, nombre, *, positivo=False):
    if isinstance(valor, bool) or valor is None or not isinstance(valor, (int, str, float, Decimal)):
        raise ValidacionError(f"{nombre} debe ser un importe válido")
    try:
        n = Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValidacionError(f"{nombre} debe ser un importe válido") from exc
    if not n.is_finite() or n < 0 or (positivo and n <= 0) or n > Decimal("999999999999.99"):
        raise ValidacionError(f"{nombre} está fuera del rango permitido")
    if n.as_tuple().exponent < -2 and n != n.quantize(Decimal("0.01")):
        raise ValidacionError(f"{nombre} admite como máximo dos decimales")
    return n


def porcentaje(valor, nombre="prioridad_score"):
    n = dinero(valor, nombre)
    if n > 100:
        raise ValidacionError(f"{nombre} debe estar entre 0 y 100")
    return n


def uuid_opcional(valor, nombre):
    if valor is None or valor == "":
        return None
    try:
        return str(UUID(str(valor)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidacionError(f"{nombre} debe ser un UUID válido") from exc


def fecha_opcional(valor, nombre):
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        raise ValidacionError(f"{nombre} debe tener formato AAAA-MM-DD")
    try:
        parsed = date.fromisoformat(valor)
    except ValueError as exc:
        raise ValidacionError(f"{nombre} debe tener formato AAAA-MM-DD") from exc
    if parsed.isoformat() != valor:
        raise ValidacionError(f"{nombre} debe tener formato AAAA-MM-DD")
    return parsed


def opcion(valor, nombre, opciones):
    if valor not in opciones:
        raise ValidacionError(f"{nombre} debe ser uno de: {', '.join(opciones)}")
    return valor


def paginacion(query, *, defecto=50, maximo=200):
    q = objeto(query or {}, "query")
    limite = entero(q.get("limite", defecto), "limite", minimo=1, maximo=maximo)
    pagina = entero(q.get("pagina", 1), "pagina", minimo=1, maximo=100_000)
    return pagina, limite


def id_ruta(event):
    return entero((event.get("pathParameters") or {}).get("id"), "id", minimo=1, maximo=9_223_372_036_854_775_807)


def requeridos(c, nombres):
    faltan = [k for k in nombres if k not in c or c[k] is None or c[k] == ""]
    if faltan:
        raise ValidacionError(f"Faltan campos obligatorios: {', '.join(faltan)}")


def fechas_coherentes(inicio, fin):
    if inicio and fin and fin < inicio:
        raise ValidacionError("fecha_fin_estimada no puede ser anterior a fecha_inicio")
