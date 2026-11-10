"""API de proyectos: edición autorizada y eventos publicados desde outbox."""
import uuid

from psycopg.rows import dict_row

from src import db, roles
from src.api import endpoint
from src.dominio import salud
from src.eventos import encolar, intentar_publicar
from src.respuesta import cuerpo_de, error, ok, tenant_de, usuario_de
from src.validacion import (campos_permitidos, dinero, entero, fechas_coherentes,
                            fecha_opcional, id_ruta, opcion, paginacion,
                            porcentaje, requeridos, texto, uuid_opcional,
                            ValidacionError)

ESTADOS = ("propuesto", "aprobado", "en_curso", "en_pausa", "cerrado", "cancelado")
SALUDES = ("verde", "ambar", "rojo")
CAMPOS = ("nombre", "descripcion", "estado", "prioridad_score", "responsable_id",
          "fecha_inicio", "fecha_fin_estimada", "presupuesto_estimado")


def _normalizar_cambios(c):
    campos_permitidos(c, CAMPOS + ("motivo",))
    cambios = {k: c[k] for k in CAMPOS if k in c}
    for campo in ("nombre", "descripcion"):
        if campo in cambios:
            cambios[campo] = texto(cambios[campo], campo, maximo=1000 if campo == "descripcion" else 200,
                                   obligatorio=(campo == "nombre"))
    if "estado" in cambios:
        cambios["estado"] = opcion(cambios["estado"], "estado", ESTADOS)
    if "prioridad_score" in cambios:
        cambios["prioridad_score"] = porcentaje(cambios["prioridad_score"])
    if "responsable_id" in cambios:
        cambios["responsable_id"] = uuid_opcional(cambios["responsable_id"], "responsable_id")
    for campo in ("fecha_inicio", "fecha_fin_estimada"):
        if campo in cambios:
            cambios[campo] = fecha_opcional(cambios[campo], campo)
    if "presupuesto_estimado" in cambios:
        cambios["presupuesto_estimado"] = dinero(cambios["presupuesto_estimado"], "presupuesto_estimado")
    return cambios


@endpoint
def listar(event, context):
    q = event.get("queryStringParameters") or {}
    pagina, limite = paginacion(q)
    filtros, valores = ["TRUE"], []
    if q.get("estado"):
        filtros.append("p.estado = %s")
        valores.append(opcion(q["estado"], "estado", ESTADOS))
    if q.get("portafolio_id"):
        filtros.append("p.portafolio_id = %s")
        valores.append(entero(q["portafolio_id"], "portafolio_id", minimo=1))
    if q.get("salud"):
        filtros.append("p.salud = %s")
        valores.append(opcion(q["salud"], "salud", SALUDES))
    if "en_riesgo" in q:
        if q["en_riesgo"] not in ("true", "false"):
            raise ValidacionError("en_riesgo debe ser 'true' o 'false'")
        filtros.append("p.en_riesgo = %s")
        valores.append(q["en_riesgo"] == "true")
    where = " AND ".join(filtros)  # solo fragmentos SQL predefinidos
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM proyectos p WHERE {where}", valores)
            total = cur.fetchone()["n"]
            cur.execute(f"""
                SELECT p.id, p.codigo, p.nombre, p.estado, p.prioridad_score,
                       p.avance_pct, p.salud, p.en_riesgo, p.responsable_id,
                       p.fecha_inicio, p.fecha_fin_estimada,
                       p.presupuesto_estimado, pf.nombre AS portafolio
                FROM proyectos p JOIN portafolios pf ON pf.id = p.portafolio_id
                WHERE {where} ORDER BY p.prioridad_score DESC, p.id
                LIMIT %s OFFSET %s
            """, valores + [limite, (pagina - 1) * limite])
            filas = cur.fetchall()
    return ok(filas, meta={"total": total, "pagina": pagina, "limite": limite})


@endpoint
def obtener(event, context):
    pid = id_ruta(event)
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT p.*, pf.nombre AS portafolio
                FROM proyectos p JOIN portafolios pf ON pf.id = p.portafolio_id
                WHERE p.id = %s
            """, (pid,))
            proyecto = cur.fetchone()
            if not proyecto:
                return error("NO_ENCONTRADO", "Proyecto no encontrado", 404)
            cur.execute("""
                SELECT estado_anterior, estado_nuevo, avance_pct, salud,
                       motivo, cambiado_en
                FROM historial_estado WHERE proyecto_id = %s
                ORDER BY cambiado_en DESC, id DESC LIMIT 20
            """, (pid,))
            proyecto["historial"] = cur.fetchall()
    return ok(proyecto)


@endpoint
def crear(event, context):
    negado = roles.exige(event, roles.PMO, roles.PM, roles.ADMIN)
    if negado:
        return negado
    tenant = tenant_de(event)
    c = cuerpo_de(event)
    campos_permitidos(c, ("portafolio_id", "codigo") + CAMPOS)
    requeridos(c, ("portafolio_id", "nombre"))
    portafolio_id = entero(c["portafolio_id"], "portafolio_id", minimo=1)
    cambios = _normalizar_cambios({k: v for k, v in c.items() if k in CAMPOS})
    if "prioridad_score" in cambios and roles.rol_de(event) not in (roles.PMO, roles.ADMIN):
        return error("ROL_INSUFICIENTE", "Solo PMO o admin pueden priorizar", 403)
    inicio, fin = cambios.get("fecha_inicio"), cambios.get("fecha_fin_estimada")
    fechas_coherentes(inicio, fin)
    codigo = texto(c.get("codigo", f"PRY-{uuid.uuid4().hex[:12].upper()}"), "codigo", maximo=50)
    with db.conexion(tenant) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT 1 FROM portafolios WHERE id = %s AND activo", (portafolio_id,))
            if not cur.fetchone():
                return error("PORTAFOLIO_INVALIDO", "Portafolio inexistente o inactivo", 400)
            cur.execute("""
                INSERT INTO proyectos
                    (portafolio_id, codigo, nombre, descripcion, estado, prioridad_score,
                     responsable_id, fecha_inicio, fecha_fin_estimada, presupuesto_estimado)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (portafolio_id, codigo, cambios["nombre"], cambios.get("descripcion"),
                  cambios.get("estado", "propuesto"), cambios.get("prioridad_score", 0),
                  cambios.get("responsable_id"), inicio, fin,
                  cambios.get("presupuesto_estimado", 0)))
            proyecto = cur.fetchone()
            nueva_salud = salud(proyecto["avance_pct"], proyecto["en_riesgo"], proyecto["estado"])
            if nueva_salud != proyecto["salud"]:
                cur.execute("UPDATE proyectos SET salud=%s WHERE id=%s RETURNING *",
                            (nueva_salud, proyecto["id"]))
                proyecto = cur.fetchone()
            cur.execute("""
                INSERT INTO historial_estado
                    (proyecto_id, estado_nuevo, motivo, cambiado_por)
                VALUES (%s,%s,'creacion',%s)
            """, (proyecto["id"], proyecto["estado"], usuario_de(event)))
            evento_id = encolar(cur, "proyecto.creado", tenant, {
                "proyecto_id": proyecto["id"], "codigo": proyecto["codigo"],
                "nombre": proyecto["nombre"], "portafolio_id": proyecto["portafolio_id"],
                "presupuesto_estimado": float(proyecto["presupuesto_estimado"]),
                "responsable_id": str(proyecto["responsable_id"]) if proyecto["responsable_id"] else None,
            })
    intentar_publicar(tenant, evento_id)
    return ok(proyecto, estado=201)


@endpoint
def actualizar(event, context):
    negado = roles.exige(event, roles.PMO, roles.PM, roles.ADMIN)
    if negado:
        return negado
    tenant, pid = tenant_de(event), id_ruta(event)
    c = cuerpo_de(event)
    cambios = _normalizar_cambios(c)
    if not cambios:
        return error("SIN_CAMBIOS", "No se indicaron campos modificables")
    if "prioridad_score" in cambios and roles.rol_de(event) not in (roles.PMO, roles.ADMIN):
        return error("ROL_INSUFICIENTE", "Solo PMO o admin pueden priorizar", 403)
    motivo = texto(c.get("motivo", "cambio manual"), "motivo", maximo=500)
    evento_id = None
    with db.conexion(tenant) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM proyectos WHERE id = %s FOR UPDATE", (pid,))
            antes = cur.fetchone()
            if not antes:
                return error("NO_ENCONTRADO", "Proyecto no encontrado", 404)
            fechas_coherentes(cambios.get("fecha_inicio", antes["fecha_inicio"]),
                              cambios.get("fecha_fin_estimada", antes["fecha_fin_estimada"]))
            nuevo_estado = cambios.get("estado", antes["estado"])
            if nuevo_estado != antes["estado"]:
                cambios["salud"] = salud(antes["avance_pct"], antes["en_riesgo"], nuevo_estado)
            sets = ", ".join(f"{k} = %s" for k in cambios) + ", actualizado_en = now()"
            cur.execute(f"UPDATE proyectos SET {sets} WHERE id = %s RETURNING *",
                        list(cambios.values()) + [pid])
            proyecto = cur.fetchone()
            if nuevo_estado != antes["estado"]:
                cur.execute("""
                    INSERT INTO historial_estado (proyecto_id, estado_anterior,
                        estado_nuevo, avance_pct, salud, motivo, cambiado_por)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (pid, antes["estado"], nuevo_estado, proyecto["avance_pct"],
                      proyecto["salud"], motivo, usuario_de(event)))
            if "prioridad_score" in cambios and proyecto["prioridad_score"] != antes["prioridad_score"]:
                evento_id = encolar(cur, "proyecto.priorizado", tenant, {
                    "proyecto_id": pid, "prioridad_score": float(proyecto["prioridad_score"]),
                })
    if evento_id:
        intentar_publicar(tenant, evento_id)
    return ok(proyecto)
