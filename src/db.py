"""Conexión PostgreSQL con selección de schema exclusivamente por identidad JWT."""
import os
from contextlib import contextmanager

import psycopg

from src import secretos, tenants

_password = None
_expira = 0


def _credenciales():
    """Usuario y clave del servicio, desde Secrets Manager.

    Antes la clave salia de SSM y el usuario venia grabado en el entorno de
    la Lambda. Ahora los dos viven en el mismo secreto JSON y el entorno solo
    lleva su nombre.
    """
    if os.environ.get("DB_PASSWORD"):  # solo para pruebas locales
        return os.environ["DB_USER"], os.environ["DB_PASSWORD"]
    nombre = os.environ["DB_SECRETO"]
    return secretos.campo(nombre, "username"), secretos.campo(nombre, "password")


def _parametros():
    _usuario, _clave = _credenciales()
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ.get("DB_NAME", "ppm_portafolios"),
        "user": _usuario,
        "password": _clave,
        "connect_timeout": 5,
        "sslmode": os.environ.get("DB_SSLMODE", "require"),
        "application_name": "ms-portafolios",
    }


@contextmanager
def conexion(tenant_id):
    # La lista de tenants vive en SSM, no en el codigo: ver src/tenants.py.
    schema = tenants.schema_de(tenant_id)
    if schema is None:
        raise PermissionError("Tenant no autorizado")
    with psycopg.connect(**_parametros()) as con:
        with con.cursor() as cur:
            # set_config local: funciona solo dentro de esta transacción.
            # Nunca interpolar un tenant procedente del usuario en SQL.
            cur.execute("SELECT set_config('search_path', %s, true)", (schema,))
            cur.execute("SET LOCAL statement_timeout = '8000ms'")
            cur.execute("SET LOCAL lock_timeout = '3000ms'")
        yield con


@contextmanager
def conexion_admin():
    """Conexion SIN tenant, para crear o borrar el schema de uno nuevo.

    `conexion(tenant_id)` exige que el tenant ya este habilitado y fija el
    search_path. En el alta pasa justo lo contrario: el schema todavia no
    existe. Por eso el aprovisionador entra por aqui.
    """
    with psycopg.connect(**_parametros()) as con:
        yield con
