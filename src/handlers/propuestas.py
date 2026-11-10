"""Admisión de demanda, decisiones de PMO y simulación de priorización."""
from psycopg.rows import dict_row

from src import db, roles
from src.api import endpoint
from src.dominio import calcular_score
from src.eventos import encolar, intentar_publicar
from src.respuesta import cuerpo_de, error, ok, tenant_de, usuario_de
from src.validacion import (ValidacionError, campos_permitidos, dinero, entero,
                            id_ruta, opcion, paginacion, requeridos, texto,
                            uuid_opcional)

ESTADOS = ("pendiente", "aprobada", "rechazada")
CAMPOS = ("portafolio_id", "nombre", "problema", "costo_estimado",
          "beneficio_esperado", "valor_estrategico", "urgencia", "riesgo")


@endpoint
def listar(event, context):
    q = event.get("queryStringParameters") or {}
    estado = opcion(q.get("estado", "pendiente"), "estado", ESTADOS)
    pagina, limite = paginacion(q)
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT COUNT(*) AS n FROM propuestas WHERE estado = %s", (estado,))
            total = cur.fetchone()["n"]
            cur.execute("""
                SELECT pr.*, pf.nombre AS portafolio, pf.presupuesto_disponible
                FROM propuestas pr JOIN portafolios pf ON pf.id = pr.portafolio_id
                WHERE pr.estado = %s
                ORDER BY pr.score DESC, pr.creado_en, pr.id
                LIMIT %s OFFSET %s
            """, (estado, limite, (pagina - 1) * limite))
            filas = cur.fetchall()
    return ok(filas, meta={"total": total, "estado": estado,
                            "pagina": pagina, "limite": limite})


@endpoint
def crear(event, context):
    tenant = tenant_de(event)
    c = cuerpo_de(event)
    campos_permitidos(c, CAMPOS)
    requeridos(c, CAMPOS)
    pid = entero(c["portafolio_id"], "portafolio_id", minimo=1)
    nombre = texto(c["nombre"], "nombre")
    problema = texto(c["problema"], "problema", maximo=2000)
    costo = dinero(c["costo_estimado"], "costo_estimado", positivo=True)
    beneficio = dinero(c["beneficio_esperado"], "beneficio_esperado")
    criterios = {k: entero(c[k], k, minimo=1, maximo=5)
                 for k in ("valor_estrategico", "urgencia", "riesgo")}
    score = calcular_score(**criterios, costo=costo, beneficio=beneficio)
    with db.conexion(tenant) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT 1 FROM portafolios WHERE id = %s AND activo", (pid,))
            if not cur.fetchone():
                return error("PORTAFOLIO_INVALIDO", "Portafolio inexistente o inactivo", 400)
            cur.execute("""
                INSERT INTO propuestas
                    (portafolio_id, nombre, problema, costo_estimado, beneficio_esperado,
                     valor_estrategico, urgencia, riesgo, score, solicitante_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *
            """, (pid, nombre, problema, costo, beneficio,
                  criterios["valor_estrategico"], criterios["urgencia"],
                  criterios["riesgo"], score, usuario_de(event)))
            propuesta = cur.fetchone()
    return ok(propuesta, estado=201)


@endpoint
def aprobar(event, context):
    negado = roles.exige(event, roles.PMO, roles.ADMIN)
    if negado:
        return negado
    tenant, pid = tenant_de(event), id_ruta(event)
    c = cuerpo_de(event)
    campos_permitidos(c, ("responsable_id", "motivo"))
    responsable = uuid_opcional(c.get("responsable_id"), "responsable_id")
    motivo = texto(c.get("motivo", "aprobada por el PMO"), "motivo", maximo=500)
    with db.conexion(tenant) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM propuestas WHERE id = %s FOR UPDATE", (pid,))
            propuesta = cur.fetchone()
            if not propuesta:
                return error("NO_ENCONTRADO", "Propuesta no encontrada", 404)
            if propuesta["estado"] != "pendiente":
                return error("YA_DECIDIDA", "La propuesta ya tiene una decisión", 409)
            # Código único independiente de la secuencia BIGSERIAL del id.
            import uuid
            codigo = f"PRY-{uuid.uuid4().hex[:12].upper()}"
            cur.execute("""
                INSERT INTO proyectos (portafolio_id, codigo, nombre, descripcion,
                    estado, prioridad_score, responsable_id, presupuesto_estimado)
                VALUES (%s,%s,%s,%s,'aprobado',%s,%s,%s) RETURNING *
            """, (propuesta["portafolio_id"], codigo, propuesta["nombre"],
                  propuesta["problema"], propuesta["score"], responsable,
                  propuesta["costo_estimado"]))
            proyecto = cur.fetchone()
            cur.execute("""
                UPDATE propuestas SET estado='aprobada', decidido_por=%s,
                    decidido_en=now(), motivo_decision=%s, proyecto_id=%s
                WHERE id=%s
            """, (usuario_de(event), motivo, proyecto["id"], pid))
            cur.execute("""
                INSERT INTO historial_estado
                    (proyecto_id, estado_nuevo, motivo, cambiado_por)
                VALUES (%s,'aprobado','aprobacion de propuesta',%s)
            """, (proyecto["id"], usuario_de(event)))
            evento_id = encolar(cur, "proyecto.creado", tenant, {
                "proyecto_id": proyecto["id"], "codigo": proyecto["codigo"],
                "nombre": proyecto["nombre"], "portafolio_id": proyecto["portafolio_id"],
                "presupuesto_estimado": float(proyecto["presupuesto_estimado"]),
                "responsable_id": str(proyecto["responsable_id"]) if proyecto["responsable_id"] else None,
                "origen": "propuesta", "propuesta_id": pid,
            })
    intentar_publicar(tenant, evento_id)
    return ok({"propuesta_id": pid, "proyecto": proyecto}, estado=201)


@endpoint
def rechazar(event, context):
    negado = roles.exige(event, roles.PMO, roles.ADMIN)
    if negado:
        return negado
    pid = id_ruta(event)
    c = cuerpo_de(event)
    campos_permitidos(c, ("motivo",))
    requeridos(c, ("motivo",))
    motivo = texto(c["motivo"], "motivo", maximo=500)
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE propuestas SET estado='rechazada', decidido_por=%s,
                     decidido_en=now(), motivo_decision=%s
                WHERE id=%s AND estado='pendiente' RETURNING *
            """, (usuario_de(event), motivo, pid))
            propuesta = cur.fetchone()
    if not propuesta:
        return error("NO_ENCONTRADO_O_DECIDIDA", "Propuesta inexistente o ya decidida", 409)
    return ok(propuesta)


@endpoint
def priorizacion(event, context):
    q = event.get("queryStringParameters") or {}
    raw = q.get("seleccion", "")
    if not isinstance(raw, str) or len(raw) > 2000:
        raise ValidacionError("seleccion es demasiado larga")
    seleccion = set()
    for valor in raw.split(","):
        if valor.strip():
            seleccion.add(entero(valor.strip(), "seleccion", minimo=1))
    if len(seleccion) > 100:
        raise ValidacionError("Se admiten como máximo 100 propuestas seleccionadas")
    filtro, valores = "", []
    if q.get("portafolio_id"):
        filtro = "AND pr.portafolio_id = %s"
        valores.append(entero(q["portafolio_id"], "portafolio_id", minimo=1))
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute(f"""
                SELECT pr.id, pr.nombre, pr.score, pr.costo_estimado,
                    pr.beneficio_esperado, pr.valor_estrategico, pr.urgencia,
                    pr.riesgo, pf.nombre AS portafolio
                FROM propuestas pr JOIN portafolios pf ON pf.id = pr.portafolio_id
                WHERE pr.estado='pendiente' {filtro}
                ORDER BY pr.score DESC, pr.id
            """, valores)
            ranking = cur.fetchall()
            cur.execute(f"""
                SELECT COALESCE(SUM(pf.presupuesto_disponible),0) AS disponible
                FROM portafolios pf WHERE pf.activo
                {filtro.replace('pr.portafolio_id', 'pf.id')}
            """, valores)
            disponible = cur.fetchone()["disponible"]
            cur.execute(f"""
                SELECT COALESCE(SUM(p.presupuesto_estimado),0) AS comprometido
                FROM proyectos p WHERE p.estado IN ('aprobado','en_curso')
                {filtro.replace('pr.portafolio_id', 'p.portafolio_id')}
            """, valores)
            comprometido = cur.fetchone()["comprometido"]
    ids = {p["id"] for p in ranking}
    if seleccion - ids:
        raise ValidacionError("La selección contiene propuestas no disponibles")
    from decimal import Decimal
    costo = sum((p["costo_estimado"] for p in ranking if p["id"] in seleccion), Decimal(0))
    for p in ranking:
        p["seleccionada"] = p["id"] in seleccion
    holgura = disponible - comprometido
    return ok(ranking, meta={
        "total": len(ranking), "presupuesto_disponible": disponible,
        "ya_comprometido": comprometido, "holgura": holgura,
        "costo_de_la_seleccion": costo, "excede_capacidad": costo > holgura,
        "exceso": max(Decimal(0), costo - holgura),
    })
