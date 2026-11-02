"""Proyectos: la unidad que se prioriza, se financia y se ejecuta."""
from psycopg.rows import dict_row

from src import db, roles
from src.eventos import publicar
from src.respuesta import cuerpo_de, error, ok, tenant_de, usuario_de

ESTADOS = ("propuesto", "aprobado", "en_curso", "en_pausa", "cerrado", "cancelado")


def listar(event, context):
    """GET /proyectos?estado=&portafolio_id=&salud=&limite=&pagina="""
    q = event.get("queryStringParameters") or {}
    limite = min(int(q.get("limite", 50)), 200)
    pagina = max(int(q.get("pagina", 1)), 1)

    filtros, valores = ["TRUE"], []
    for campo in ("estado", "portafolio_id", "salud"):
        if q.get(campo):
            filtros.append(f"p.{campo} = %s")
            valores.append(q[campo])
    if q.get("en_riesgo") == "true":
        filtros.append("p.en_riesgo")
    where = " AND ".join(filtros)

    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM proyectos p WHERE {where}", valores)
            total = cur.fetchone()["n"]
            cur.execute(f"""
                SELECT  p.id, p.codigo, p.nombre, p.estado, p.prioridad_score,
                        p.avance_pct, p.salud, p.en_riesgo, p.responsable_id,
                        p.fecha_inicio, p.fecha_fin_estimada,
                        p.presupuesto_estimado, pf.nombre AS portafolio
                FROM    proyectos p
                JOIN    portafolios pf ON pf.id = p.portafolio_id
                WHERE   {where}
                ORDER BY p.prioridad_score DESC, p.id
                LIMIT %s OFFSET %s
            """, valores + [limite, (pagina - 1) * limite])
            filas = cur.fetchall()

    return ok(filas, meta={"total": total, "pagina": pagina, "limite": limite})


def obtener(event, context):
    """GET /proyectos/{id} — con su historial de estado."""
    pid = event["pathParameters"]["id"]
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT p.*, pf.nombre AS portafolio
                FROM   proyectos p JOIN portafolios pf ON pf.id = p.portafolio_id
                WHERE  p.id = %s
            """, (pid,))
            proyecto = cur.fetchone()
            if not proyecto:
                return error("NO_ENCONTRADO", f"No existe el proyecto {pid}", 404)
            cur.execute("""
                SELECT estado_anterior, estado_nuevo, avance_pct, salud,
                       motivo, cambiado_en
                FROM   historial_estado WHERE proyecto_id = %s
                ORDER BY cambiado_en DESC LIMIT 20
            """, (pid,))
            proyecto["historial"] = cur.fetchall()
    return ok(proyecto)


def crear(event, context):
    """POST /proyectos — publica proyecto.creado.

    Ese evento hace que ms-presupuestos cree el presupuesto en 0 y que
    ms-analitico registre el proyecto en el data lake. Nadie llama a nadie.
    """
    negado = roles.exige(event, roles.PMO, roles.PM, roles.ADMIN)
    if negado:
        return negado

    tenant_id = tenant_de(event)
    c = cuerpo_de(event)
    faltan = [k for k in ("portafolio_id", "nombre") if not c.get(k)]
    if faltan:
        return error("DATOS_INCOMPLETOS", f"Falta: {', '.join(faltan)}")

    with db.conexion(tenant_id) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT 1 FROM portafolios WHERE id = %s",
                        (c["portafolio_id"],))
            if not cur.fetchone():
                return error("PORTAFOLIO_INVALIDO",
                             f"No existe el portafolio {c['portafolio_id']}")

            cur.execute("""
                INSERT INTO proyectos
                    (portafolio_id, codigo, nombre, descripcion, estado,
                     prioridad_score, responsable_id, fecha_inicio,
                     fecha_fin_estimada, presupuesto_estimado)
                VALUES (%s,
                        COALESCE(%s, 'PRY-' || LPAD(nextval('proyectos_id_seq')::text, 5, '0')),
                        %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
            """, (c["portafolio_id"], c.get("codigo"), c["nombre"].strip(),
                  c.get("descripcion"), c.get("estado", "propuesto"),
                  c.get("prioridad_score", 0), c.get("responsable_id"),
                  c.get("fecha_inicio"), c.get("fecha_fin_estimada"),
                  c.get("presupuesto_estimado", 0)))
            proyecto = cur.fetchone()

            cur.execute("""
                INSERT INTO historial_estado
                    (proyecto_id, estado_nuevo, motivo, cambiado_por)
                VALUES (%s, %s, 'creacion', %s)
            """, (proyecto["id"], proyecto["estado"], usuario_de(event)))

    # Primero el commit (al salir del with), despues el evento. Si se publicara
    # antes, un rollback dejaria a los suscriptores creyendo algo que no paso.
    publicar("proyecto.creado", tenant_id, {
        "proyecto_id": proyecto["id"],
        "codigo": proyecto["codigo"],
        "nombre": proyecto["nombre"],
        "portafolio_id": proyecto["portafolio_id"],
        "presupuesto_estimado": float(proyecto["presupuesto_estimado"]),
        "responsable_id": str(proyecto["responsable_id"]) if proyecto["responsable_id"] else None,
    })
    return ok(proyecto, estado=201)


def actualizar(event, context):
    """PUT /proyectos/{id} — cambia estado, responsable, fechas o prioridad."""
    negado = roles.exige(event, roles.PMO, roles.PM, roles.ADMIN)
    if negado:
        return negado

    tenant_id = tenant_de(event)
    pid = event["pathParameters"]["id"]
    c = cuerpo_de(event)

    permitidos = ("nombre", "descripcion", "estado", "prioridad_score",
                  "responsable_id", "fecha_inicio", "fecha_fin_estimada",
                  "presupuesto_estimado")
    cambios = {k: c[k] for k in permitidos if k in c}
    if not cambios:
        return error("SIN_CAMBIOS", f"Envia alguno de: {', '.join(permitidos)}")
    if "estado" in cambios and cambios["estado"] not in ESTADOS:
        return error("ESTADO_INVALIDO", f"Estados validos: {', '.join(ESTADOS)}")

    sets = ", ".join(f"{k} = %s" for k in cambios) + ", actualizado_en = now()"

    with db.conexion(tenant_id) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT estado FROM proyectos WHERE id = %s", (pid,))
            antes = cur.fetchone()
            if not antes:
                return error("NO_ENCONTRADO", f"No existe el proyecto {pid}", 404)

            cur.execute(f"UPDATE proyectos SET {sets} WHERE id = %s RETURNING *",
                        list(cambios.values()) + [pid])
            proyecto = cur.fetchone()

            if "estado" in cambios and cambios["estado"] != antes["estado"]:
                cur.execute("""
                    INSERT INTO historial_estado
                        (proyecto_id, estado_anterior, estado_nuevo,
                         avance_pct, salud, motivo, cambiado_por)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (pid, antes["estado"], cambios["estado"],
                      proyecto["avance_pct"], proyecto["salud"],
                      c.get("motivo", "cambio manual"), usuario_de(event)))

    if "prioridad_score" in cambios:
        publicar("proyecto.priorizado", tenant_id, {
            "proyecto_id": int(pid),
            "prioridad_score": float(proyecto["prioridad_score"]),
        })
    return ok(proyecto)
