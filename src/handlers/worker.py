"""Worker SQS: mantiene el avance y la salud de los proyectos.

Aqui esta el valor del EDA: nadie reporta el avance a mano. ms-tareas publica
que una tarea se completo y este worker recalcula el porcentaje del proyecto,
sin que los dos microservicios se conozcan.

Eventos que consume (filter policy de la cola):
    tarea.creada           -> recalcula avance
    tarea.completada       -> recalcula avance  <- el momento clave de la demo
    presupuesto.excedido   -> marca el proyecto en riesgo
"""
import json

from psycopg.rows import dict_row

from src import db


def _ya_procesado(cur, evento_id, tipo):
    """Idempotencia. SQS entrega at-least-once: sin esto el avance se infla.

    Se guarda en la misma transaccion que el cambio, asi no puede quedar un
    evento marcado como procesado cuyo efecto se revirtio.
    """
    cur.execute("""
        INSERT INTO eventos_procesados (evento_id, tipo)
        VALUES (%s, %s)
        ON CONFLICT (evento_id) DO NOTHING
        RETURNING evento_id
    """, (evento_id, tipo))
    return cur.fetchone() is None


def _salud(avance_pct, en_riesgo, estado):
    """El semaforo lo calcula el sistema, no se pone a mano.

    rojo  = el presupuesto se excedio
    ambar = esta en curso y el avance va por debajo de la mitad
    verde = todo lo demas
    """
    if en_riesgo:
        return "rojo"
    if estado == "en_curso" and float(avance_pct) < 50:
        return "ambar"
    return "verde"


def _recalcular_avance(cur, proyecto_id, total, completadas):
    """El avance es completadas/total. ms-tareas manda los dos numeros.

    Se recalcula con el total que viene en el evento en vez de acumular +1,
    porque un evento perdido o repetido no debe desviar el porcentaje para
    siempre.
    """
    pct = round(100.0 * completadas / total, 2) if total else 0.0

    cur.execute("""
        UPDATE proyectos
        SET    avance_pct = %s, actualizado_en = now()
        WHERE  id = %s
        RETURNING id, estado, en_riesgo, avance_pct, salud
    """, (pct, proyecto_id))
    fila = cur.fetchone()
    if not fila:
        return None  # el proyecto es de otro microservicio o ya no existe

    nueva = _salud(pct, fila["en_riesgo"], fila["estado"])
    if nueva != fila["salud"]:
        cur.execute("UPDATE proyectos SET salud = %s WHERE id = %s",
                    (nueva, proyecto_id))
        cur.execute("""
            INSERT INTO historial_estado
                (proyecto_id, estado_anterior, estado_nuevo, avance_pct,
                 salud, motivo)
            VALUES (%s, %s, %s, %s, %s, 'recalculo automatico por evento')
        """, (proyecto_id, fila["estado"], fila["estado"], pct, nueva))
    return pct


def _marcar_riesgo(cur, proyecto_id, ejecutado_pct):
    cur.execute("""
        UPDATE proyectos
        SET    en_riesgo = TRUE, salud = 'rojo', actualizado_en = now()
        WHERE  id = %s
        RETURNING estado, avance_pct
    """, (proyecto_id,))
    fila = cur.fetchone()
    if not fila:
        return
    cur.execute("""
        INSERT INTO historial_estado
            (proyecto_id, estado_anterior, estado_nuevo, avance_pct, salud, motivo)
        VALUES (%s, %s, %s, %s, 'rojo', %s)
    """, (proyecto_id, fila["estado"], fila["estado"], fila["avance_pct"],
          f"presupuesto ejecutado al {ejecutado_pct}%"))


def manejar(event, context):
    """Entrada del worker. Un fallo deja el lote en la cola y, tras 3
    intentos, el mensaje cae en la DLQ.
    """
    procesados, omitidos = 0, 0

    for registro in event["Records"]:
        # SQS envuelve el mensaje de SNS: el cuerpo del evento viene anidado.
        sobre = json.loads(registro["body"])
        evento = json.loads(sobre["Message"]) if "Message" in sobre else sobre

        tipo = evento["tipo"]
        tenant_id = evento["tenant_id"]
        datos = evento.get("datos", {})

        with db.conexion(tenant_id) as con:
            with con.cursor(row_factory=dict_row) as cur:
                if _ya_procesado(cur, evento["evento_id"], tipo):
                    omitidos += 1
                    continue

                if tipo in ("tarea.creada", "tarea.completada"):
                    _recalcular_avance(
                        cur,
                        datos["proyecto_id"],
                        int(datos.get("total_tareas", 0)),
                        int(datos.get("tareas_completadas", 0)),
                    )
                elif tipo == "presupuesto.excedido":
                    _marcar_riesgo(cur, datos["proyecto_id"],
                                   datos.get("ejecutado_pct", 100))
                else:
                    # La filter policy no deberia dejarlo pasar, pero si cambia
                    # se descarta sin reventar la cola.
                    omitidos += 1
                    continue

                procesados += 1

    return {"procesados": procesados, "omitidos": omitidos}
