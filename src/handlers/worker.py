"""Consumidor SQS con idempotencia ACID y reporte parcial de fallos."""
import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from psycopg.rows import dict_row

from src import db
from src.dominio import salud

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
TIPOS = ("tarea.creada", "tarea.completada", "presupuesto.excedido")


def _entero_evento(valor, nombre, minimo=0):
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        raise ValueError(f"{nombre} inválido")
    return valor


def _fecha_evento(valor):
    if not isinstance(valor, str):
        raise ValueError("El evento debe incluir ocurrido_en")
    momento = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if momento.tzinfo is None:
        raise ValueError("ocurrido_en debe incluir zona horaria")
    return momento.astimezone(timezone.utc)


def _registrar_id(cur, evento_id, tipo):
    cur.execute("""
        INSERT INTO eventos_procesados (evento_id, tipo) VALUES (%s,%s)
        ON CONFLICT (evento_id) DO NOTHING RETURNING evento_id
    """, (evento_id, tipo))
    return cur.fetchone() is not None


def _avance(cur, proyecto_id, total, completadas, ocurrido_en):
    if completadas > total:
        raise ValueError("tareas_completadas no puede superar total_tareas")
    pct = round(100.0 * completadas / total, 2) if total else 0.0
    cur.execute("""
        UPDATE proyectos
        SET avance_pct=%s, ultimo_evento_tareas_en=%s, actualizado_en=now()
        WHERE id=%s AND
             (ultimo_evento_tareas_en IS NULL OR ultimo_evento_tareas_en <= %s)
        RETURNING id, estado, en_riesgo, avance_pct, salud
    """, (pct, ocurrido_en, proyecto_id, ocurrido_en))
    proyecto = cur.fetchone()
    if not proyecto:
        cur.execute("SELECT 1 FROM proyectos WHERE id=%s", (proyecto_id,))
        if cur.fetchone():
            return "obsoleto"
        raise LookupError("Proyecto no disponible; requiere reintento")
    nueva = salud(pct, proyecto["en_riesgo"], proyecto["estado"])
    if nueva != proyecto["salud"]:
        cur.execute("UPDATE proyectos SET salud=%s WHERE id=%s", (nueva, proyecto_id))
        cur.execute("""
            INSERT INTO historial_estado
                (proyecto_id, estado_anterior, estado_nuevo, avance_pct, salud, motivo)
            VALUES (%s,%s,%s,%s,%s,'recalculo automatico por evento')
        """, (proyecto_id, proyecto["estado"], proyecto["estado"], pct, nueva))
    return "actualizado"


def _riesgo(cur, proyecto_id, ejecutado_pct):
    cur.execute("""
        UPDATE proyectos
        SET en_riesgo=TRUE, salud='rojo', actualizado_en=now()
        WHERE id=%s AND NOT en_riesgo RETURNING estado, avance_pct
    """, (proyecto_id,))
    fila = cur.fetchone()
    if not fila:
        cur.execute("SELECT 1 FROM proyectos WHERE id=%s", (proyecto_id,))
        if cur.fetchone():
            return "ya_en_riesgo"
        raise LookupError("Proyecto no disponible; requiere reintento")
    cur.execute("""
        INSERT INTO historial_estado
            (proyecto_id, estado_anterior, estado_nuevo, avance_pct, salud, motivo)
        VALUES (%s,%s,%s,%s,'rojo',%s)
    """, (proyecto_id, fila["estado"], fila["estado"], fila["avance_pct"],
          f"presupuesto ejecutado al {ejecutado_pct}%"))
    return "en_riesgo"


def procesar_registro(registro):
    sobre = json.loads(registro["body"])
    evento = json.loads(sobre["Message"]) if isinstance(sobre, dict) and "Message" in sobre else sobre
    if not isinstance(evento, dict):
        raise ValueError("Evento no es un objeto")
    tipo = evento.get("tipo")
    if tipo not in TIPOS:
        return "ignorado"
    if evento.get("version") != 1:
        raise ValueError("Versión de evento no soportada")
    evento_id = str(UUID(evento["evento_id"]))
    tenant = evento["tenant_id"]
    if tenant not in db.SCHEMAS:
        raise ValueError("Tenant de evento desconocido")
    datos = evento.get("datos")
    if not isinstance(datos, dict):
        raise ValueError("datos debe ser objeto")
    proyecto_id = _entero_evento(datos.get("proyecto_id"), "proyecto_id", minimo=1)
    if tipo.startswith("tarea."):
        ocurrido_en = _fecha_evento(evento.get("ocurrido_en"))
        total = _entero_evento(datos.get("total_tareas"), "total_tareas")
        completadas = _entero_evento(datos.get("tareas_completadas"), "tareas_completadas")
    else:
        ejecutado_pct = datos.get("ejecutado_pct", 100)
        if isinstance(ejecutado_pct, bool) or not isinstance(ejecutado_pct, (int, float)) or not 0 <= ejecutado_pct <= 100000:
            raise ValueError("ejecutado_pct inválido")
    with db.conexion(tenant) as con:
        with con.cursor(row_factory=dict_row) as cur:
            if not _registrar_id(cur, evento_id, tipo):
                return "duplicado"
            if tipo.startswith("tarea."):
                return _avance(cur, proyecto_id, total, completadas, ocurrido_en)
            return _riesgo(cur, proyecto_id, ejecutado_pct)


def manejar(event, context):
    """Solo reintenta mensajes fallidos; los demás salen del lote con éxito."""
    fallos = []
    for registro in event.get("Records", []):
        try:
            procesar_registro(registro)
        except Exception:
            # No incluir cuerpo de mensajes / credenciales en CloudWatch.
            log.exception("Fallo evento SQS; mensaje_id=%s", registro.get("messageId"))
            fallos.append({"itemIdentifier": registro["messageId"]})
    return {"batchItemFailures": fallos}
