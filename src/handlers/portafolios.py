"""Portafolios: el nivel que agrupa proyectos por objetivo estrategico."""
from psycopg.rows import dict_row

from src import db, roles
from src.respuesta import cuerpo_de, error, ok, tenant_de


def listar(event, context):
    """GET /portafolios — con el resumen de sus proyectos."""
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT  p.id, p.nombre, p.objetivo_estrategico,
                        p.presupuesto_disponible, p.capacidad_horas_semana,
                        COUNT(pr.id)                             AS proyectos,
                        COUNT(pr.id) FILTER (WHERE pr.estado = 'en_curso') AS en_curso,
                        COUNT(pr.id) FILTER (WHERE pr.en_riesgo)           AS en_riesgo,
                        COALESCE(SUM(pr.presupuesto_estimado), 0)          AS comprometido,
                        COALESCE(ROUND(AVG(pr.avance_pct), 1), 0)          AS avance_promedio
                FROM    portafolios p
                LEFT JOIN proyectos pr ON pr.portafolio_id = p.id
                WHERE   p.activo
                GROUP BY p.id
                ORDER BY p.nombre
            """)
            filas = cur.fetchall()
    return ok(filas, meta={"total": len(filas)})


def obtener(event, context):
    """GET /portafolios/{id}"""
    pid = event["pathParameters"]["id"]
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM portafolios WHERE id = %s", (pid,))
            portafolio = cur.fetchone()
            if not portafolio:
                return error("NO_ENCONTRADO", f"No existe el portafolio {pid}", 404)
            cur.execute("""
                SELECT id, codigo, nombre, estado, prioridad_score, avance_pct,
                       salud, en_riesgo, presupuesto_estimado
                FROM   proyectos WHERE portafolio_id = %s
                ORDER BY prioridad_score DESC
            """, (pid,))
            portafolio["proyectos"] = cur.fetchall()
    return ok(portafolio)


def crear(event, context):
    """POST /portafolios — solo el PMO define portafolios."""
    negado = roles.exige(event, roles.PMO, roles.ADMIN)
    if negado:
        return negado

    c = cuerpo_de(event)
    faltan = [k for k in ("nombre", "objetivo_estrategico") if not c.get(k)]
    if faltan:
        return error("DATOS_INCOMPLETOS", f"Falta: {', '.join(faltan)}")

    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO portafolios
                    (nombre, descripcion, objetivo_estrategico,
                     presupuesto_disponible, capacidad_horas_semana)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (nombre) DO NOTHING
                RETURNING *
            """, (c["nombre"].strip(), c.get("descripcion"),
                  c["objetivo_estrategico"].strip(),
                  c.get("presupuesto_disponible", 0),
                  c.get("capacidad_horas_semana", 0)))
            fila = cur.fetchone()

    if not fila:
        return error("NOMBRE_DUPLICADO", f"Ya existe un portafolio '{c['nombre']}'", 409)
    return ok(fila, estado=201)
