"""Admision de demanda: cualquiera propone, el PMO decide.

Es la puerta de entrada del portafolio y lo que distingue un PPM de una lista
de proyectos. Planview lo llama demand intake; Smartsheet, flujos de admision
y aprobacion.
"""
from psycopg.rows import dict_row

from src import db, roles
from src.eventos import publicar
from src.respuesta import cuerpo_de, error, ok, tenant_de, usuario_de


def calcular_score(valor_estrategico, urgencia, riesgo, costo, beneficio):
    """Score de 0 a 100. Mas valor y beneficio suben; mas costo y riesgo bajan.

    La formula es deliberadamente simple y explicable: el PMO tiene que poder
    entender por que un proyecto quedo arriba. Planview lo vende como priorizar
    "contra criterios de inversion, no por intuicion".

        beneficio/costo se topa en 3x para que un caso optimista no domine
        el ranking entero.
    """
    retorno = min(float(beneficio) / float(costo), 3.0) if float(costo) > 0 else 3.0
    bruto = (
        float(valor_estrategico) * 8      # peso 40 sobre 5
        + float(urgencia) * 5             # peso 25 sobre 5
        + retorno * 8.33                  # peso 25 sobre 3
        - float(riesgo) * 2               # penalidad hasta 10
    )
    return round(max(0.0, min(bruto, 100.0)), 2)


def listar(event, context):
    """GET /propuestas?estado=pendiente — la bandeja de admision, rankeada."""
    q = event.get("queryStringParameters") or {}
    estado = q.get("estado", "pendiente")

    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT  pr.*, pf.nombre AS portafolio,
                        pf.presupuesto_disponible
                FROM    propuestas pr
                JOIN    portafolios pf ON pf.id = pr.portafolio_id
                WHERE   pr.estado = %s
                ORDER BY pr.score DESC, pr.creado_en
            """, (estado,))
            filas = cur.fetchall()
    return ok(filas, meta={"total": len(filas), "estado": estado})


def crear(event, context):
    """POST /propuestas — cualquier rol puede proponer."""
    tenant_id = tenant_de(event)
    c = cuerpo_de(event)

    obligatorios = ("portafolio_id", "nombre", "problema", "costo_estimado",
                    "beneficio_esperado", "valor_estrategico", "urgencia", "riesgo")
    faltan = [k for k in obligatorios if c.get(k) in (None, "")]
    if faltan:
        return error("DATOS_INCOMPLETOS", f"Falta: {', '.join(faltan)}")

    for campo in ("valor_estrategico", "urgencia", "riesgo"):
        if not 1 <= int(c[campo]) <= 5:
            return error("FUERA_DE_RANGO", f"{campo} va de 1 a 5")
    if float(c["costo_estimado"]) <= 0:
        return error("COSTO_INVALIDO", "El costo estimado debe ser mayor que 0")

    score = calcular_score(c["valor_estrategico"], c["urgencia"], c["riesgo"],
                           c["costo_estimado"], c["beneficio_esperado"])

    with db.conexion(tenant_id) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO propuestas
                    (portafolio_id, nombre, problema, costo_estimado,
                     beneficio_esperado, valor_estrategico, urgencia, riesgo,
                     score, solicitante_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (c["portafolio_id"], c["nombre"].strip(), c["problema"].strip(),
                  c["costo_estimado"], c["beneficio_esperado"],
                  c["valor_estrategico"], c["urgencia"], c["riesgo"],
                  score, usuario_de(event)))
            propuesta = cur.fetchone()
    return ok(propuesta, estado=201)


def aprobar(event, context):
    """POST /propuestas/{id}/aprobar — solo el PMO. La convierte en proyecto.

    Publica proyecto.creado, igual que la creacion directa: para los
    suscriptores es indistinguible de donde vino el proyecto.
    """
    negado = roles.exige(event, roles.PMO, roles.ADMIN)
    if negado:
        return negado

    tenant_id = tenant_de(event)
    pid = event["pathParameters"]["id"]
    c = cuerpo_de(event)

    with db.conexion(tenant_id) as con:
        with con.cursor(row_factory=dict_row) as cur:
            # FOR UPDATE: dos PMO aprobando la misma propuesta a la vez crearian
            # dos proyectos duplicados.
            cur.execute("SELECT * FROM propuestas WHERE id = %s FOR UPDATE", (pid,))
            propuesta = cur.fetchone()
            if not propuesta:
                return error("NO_ENCONTRADO", f"No existe la propuesta {pid}", 404)
            if propuesta["estado"] != "pendiente":
                return error("YA_DECIDIDA",
                             f"La propuesta ya fue {propuesta['estado']}", 409)

            cur.execute("""
                INSERT INTO proyectos
                    (portafolio_id, codigo, nombre, descripcion, estado,
                     prioridad_score, responsable_id, presupuesto_estimado)
                VALUES (%s,
                        'PRY-' || LPAD(nextval('proyectos_id_seq')::text, 5, '0'),
                        %s, %s, 'aprobado', %s, %s, %s)
                RETURNING *
            """, (propuesta["portafolio_id"], propuesta["nombre"],
                  propuesta["problema"], propuesta["score"],
                  c.get("responsable_id"), propuesta["costo_estimado"]))
            proyecto = cur.fetchone()

            cur.execute("""
                UPDATE propuestas
                SET    estado = 'aprobada', decidido_por = %s, decidido_en = now(),
                       motivo_decision = %s, proyecto_id = %s
                WHERE  id = %s
            """, (usuario_de(event), c.get("motivo", "aprobada por el PMO"),
                  proyecto["id"], pid))

            cur.execute("""
                INSERT INTO historial_estado
                    (proyecto_id, estado_nuevo, motivo, cambiado_por)
                VALUES (%s, 'aprobado', 'aprobacion de propuesta', %s)
            """, (proyecto["id"], usuario_de(event)))

    publicar("proyecto.creado", tenant_id, {
        "proyecto_id": proyecto["id"],
        "codigo": proyecto["codigo"],
        "nombre": proyecto["nombre"],
        "portafolio_id": proyecto["portafolio_id"],
        "presupuesto_estimado": float(proyecto["presupuesto_estimado"]),
        "origen": "propuesta",
        "propuesta_id": int(pid),
    })
    return ok({"propuesta_id": int(pid), "proyecto": proyecto}, estado=201)


def rechazar(event, context):
    """POST /propuestas/{id}/rechazar — solo el PMO."""
    negado = roles.exige(event, roles.PMO, roles.ADMIN)
    if negado:
        return negado

    pid = event["pathParameters"]["id"]
    c = cuerpo_de(event)
    if not c.get("motivo"):
        return error("MOTIVO_REQUERIDO", "Un rechazo necesita motivo")

    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                UPDATE propuestas
                SET    estado = 'rechazada', decidido_por = %s,
                       decidido_en = now(), motivo_decision = %s
                WHERE  id = %s AND estado = 'pendiente'
                RETURNING *
            """, (usuario_de(event), c["motivo"], pid))
            propuesta = cur.fetchone()

    if not propuesta:
        return error("NO_ENCONTRADO",
                     f"La propuesta {pid} no existe o ya fue decidida", 404)
    return ok(propuesta)


def priorizacion(event, context):
    """GET /priorizacion?portafolio_id=&seleccion=1,5,9

    El ranking solo no sirve: devuelve tambien cuanto consumen juntas las
    propuestas seleccionadas y si se pasan de lo disponible. Es el
    scenario planning de Planview en su version minima.
    """
    q = event.get("queryStringParameters") or {}
    seleccion = [int(x) for x in q.get("seleccion", "").split(",") if x.strip().isdigit()]

    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            filtro, valores = "", []
            if q.get("portafolio_id"):
                filtro = "AND pr.portafolio_id = %s"
                valores.append(q["portafolio_id"])

            cur.execute(f"""
                SELECT pr.id, pr.nombre, pr.score, pr.costo_estimado,
                       pr.beneficio_esperado, pr.valor_estrategico,
                       pr.urgencia, pr.riesgo, pf.nombre AS portafolio
                FROM   propuestas pr
                JOIN   portafolios pf ON pf.id = pr.portafolio_id
                WHERE  pr.estado = 'pendiente' {filtro}
                ORDER BY pr.score DESC
            """, valores)
            ranking = cur.fetchall()

            cur.execute(f"""
                SELECT COALESCE(SUM(pf.presupuesto_disponible), 0) AS disponible
                FROM   portafolios pf
                WHERE  pf.activo {filtro.replace('pr.portafolio_id', 'pf.id')}
            """, valores)
            disponible = float(cur.fetchone()["disponible"])

            cur.execute(f"""
                SELECT COALESCE(SUM(p.presupuesto_estimado), 0) AS comprometido
                FROM   proyectos p
                WHERE  p.estado IN ('aprobado', 'en_curso') {filtro.replace('pr.', 'p.')}
            """, valores)
            comprometido = float(cur.fetchone()["comprometido"])

    for p in ranking:
        p["seleccionada"] = p["id"] in seleccion

    costo_seleccion = sum(float(p["costo_estimado"]) for p in ranking if p["seleccionada"])
    holgura = disponible - comprometido

    return ok(ranking, meta={
        "total": len(ranking),
        "presupuesto_disponible": disponible,
        "ya_comprometido": comprometido,
        "holgura": round(holgura, 2),
        "costo_de_la_seleccion": round(costo_seleccion, 2),
        "excede_capacidad": costo_seleccion > holgura,
        "exceso": round(max(0.0, costo_seleccion - holgura), 2),
    })
