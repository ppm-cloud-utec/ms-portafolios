"""Conexión PostgreSQL con selección de schema exclusivamente por identidad JWT."""
import os
import time
from contextlib import contextmanager

import boto3
import psycopg

SCHEMAS = {"t-acme": "t_acme", "t-globex": "t_globex", "t-initech": "t_initech"}
_password = None
_expira = 0


def password_bd():
    """No resuelve el secreto en CloudFormation ni lo graba en env de Lambda."""
    global _password, _expira
    # Solo para pruebas locales; en AWS se configura DB_PASSWORD_PARAM.
    if os.environ.get("DB_PASSWORD"):
        return os.environ["DB_PASSWORD"]
    parametro = os.environ["DB_PASSWORD_PARAM"]
    if _password is None or time.monotonic() >= _expira:
        _password = boto3.client("ssm").get_parameter(
            Name=parametro, WithDecryption=True
        )["Parameter"]["Value"]
        _expira = time.monotonic() + 300
    return _password


def _parametros():
    return {
        "host": os.environ["DB_HOST"],
        "port": int(os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ.get("DB_NAME", "ppm_portafolios"),
        "user": os.environ["DB_USER"],
        "password": password_bd(),
        "connect_timeout": 5,
        "sslmode": os.environ.get("DB_SSLMODE", "require"),
        "application_name": "ms-portafolios",
    }


@contextmanager
def conexion(tenant_id):
    schema = SCHEMAS.get(tenant_id)
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
