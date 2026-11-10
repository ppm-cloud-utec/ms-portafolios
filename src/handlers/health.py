"""Readiness autenticada: confirma acceso real a la BD del tenant."""
from src import db
from src.api import endpoint
from src.respuesta import ok, tenant_de


@endpoint
def manejar(event, context):
    tenant = tenant_de(event)
    with db.conexion(tenant) as con:
        with con.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    return ok({"servicio": "ms-portafolios", "estado": "ok", "tenant": tenant})
