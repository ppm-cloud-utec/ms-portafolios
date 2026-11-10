"""Outbox transaccional: cambios de BD y evento se confirman juntos.

El envío SNS sucede DESPUÉS del commit, con reintentos programados. Los
consumidores deben deduplicar por evento_id (entrega al menos una vez).
"""
import json
import logging
import os
import uuid
from uuid import UUID
from datetime import datetime, timezone

import boto3
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from src import db

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def encolar(cur, tipo, tenant_id, datos):
    """Se llama con el cursor de la misma transacción de la escritura."""
    evento_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO outbox_eventos (evento_id, tipo, datos) VALUES (%s, %s, %s)",
        (evento_id, tipo, Jsonb(datos)),
    )
    return evento_id


def publicar_pendientes(tenant_id, ids=None, *, limite=20):
    """Publica y marca en la misma transacción. Si hay crash, puede duplicar,
    nunca perder un evento persistido. SKIP LOCKED evita publicadores simultáneos.
    """
    publicados = 0
    with db.conexion(tenant_id) as con:
        with con.cursor(row_factory=dict_row) as cur:
            if ids:
                cur.execute("""
                    SELECT * FROM outbox_eventos
                    WHERE publicado_en IS NULL AND evento_id = ANY(%s::uuid[])
                    ORDER BY creado_en, evento_id
                    LIMIT %s FOR UPDATE SKIP LOCKED
                """, ([UUID(str(v)) for v in ids], limite))
            else:
                cur.execute("""
                    SELECT * FROM outbox_eventos
                    WHERE publicado_en IS NULL
                    ORDER BY creado_en, evento_id
                    LIMIT %s FOR UPDATE SKIP LOCKED
                """, (limite,))
            filas = cur.fetchall()
            for fila in filas:
                mensaje = {
                    "evento_id": str(fila["evento_id"]), "tipo": fila["tipo"],
                    "version": 1, "tenant_id": tenant_id,
                    "ocurrido_en": fila["creado_en"].astimezone(timezone.utc).isoformat(),
                    "origen": os.getenv("SERVICIO", "ms-portafolios"),
                    "datos": fila["datos"],
                }
                try:
                    boto3.client("sns").publish(
                        TopicArn=os.environ["SNS_TOPIC_ARN"],
                        Message=json.dumps(mensaje, ensure_ascii=False, default=str),
                        MessageAttributes={
                            "tipo": {"DataType": "String", "StringValue": fila["tipo"]},
                            "tenant_id": {"DataType": "String", "StringValue": tenant_id},
                            "origen": {"DataType": "String", "StringValue": mensaje["origen"]},
                        },
                    )
                    cur.execute("""
                        UPDATE outbox_eventos
                        SET publicado_en = now(), intentos = intentos + 1
                        WHERE evento_id = %s
                    """, (fila["evento_id"],))
                    publicados += 1
                except Exception as exc:
                    # Sin guardar el mensaje de error (puede contener datos sensibles).
                    cur.execute("""
                        UPDATE outbox_eventos
                        SET intentos = intentos + 1, ultimo_error = %s
                        WHERE evento_id = %s
                    """, (type(exc).__name__, fila["evento_id"]))
                    log.warning("SNS no disponible; evento pendiente para reintento: %s", fila["evento_id"])
                    # Sigue con los otros eventos; ninguno se perderá.
    return publicados


def intentar_publicar(tenant_id, evento_id):
    """Fast-path para demo. Nunca hace fallar una escritura ya confirmada."""
    try:
        publicar_pendientes(tenant_id, [evento_id])
    except Exception:
        log.exception("No se pudo publicar inmediatamente; reintentará el cron")
