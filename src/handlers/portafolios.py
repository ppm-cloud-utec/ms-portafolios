"""Portafolios: objetivos estratégicos, capacidad y presupuestos agregados."""
from psycopg.rows import dict_row

from src import db, roles
from src.api import endpoint
from src.respuesta import cuerpo_de, error, ok, tenant_de
from src.validacion import (campos_permitidos, dinero, entero, id_ruta,
                            requeridos, texto)


@endpoint
def listar(event, context):
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT p.id, p.nombre, p.objetivo_estrategico,
                       p.presupuesto_disponible, p.capacidad_horas_semana,
                       COUNT(pr.id) AS proyectos,
                       COUNT(pr.id) FILTER (WHERE pr.estado = 'en_curso') AS en_curso,
                       COUNT(pr.id) FILTER (WHERE pr.en_riesgo) AS en_riesgo,
                       COALESCE(SUM(pr.presupuesto_estimado), 0) AS comprometido,
                       COALESCE(ROUND(AVG(pr.avance_pct), 1), 0) AS avance_promedio
                FROM portafolios p
                LEFT JOIN proyectos pr ON pr.portafolio_id = p.id
                WHERE p.activo
                GROUP BY p.id ORDER BY p.nombre
            """)
            filas = cur.fetchall()
    return ok(filas, meta={"total": len(filas)})


@endpoint
def obtener(event, context):
    pid = id_ruta(event)
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM portafolios WHERE id = %s", (pid,))
            portafolio = cur.fetchone()
            if not portafolio:
                return error("NO_ENCONTRADO", "Portafolio no encontrado", 404)
            cur.execute("""
                SELECT id, codigo, nombre, estado, prioridad_score, avance_pct,
                       salud, en_riesgo, presupuesto_estimado
                FROM proyectos WHERE portafolio_id = %s
                ORDER BY prioridad_score DESC, id
            """, (pid,))
            portafolio["proyectos"] = cur.fetchall()
    return ok(portafolio)


@endpoint
def crear(event, context):
    negado = roles.exige(event, roles.PMO, roles.ADMIN)
    if negado:
        return negado
    c = cuerpo_de(event)
    permitidos = ("nombre", "descripcion", "objetivo_estrategico",
                  "presupuesto_disponible", "capacidad_horas_semana")
    campos_permitidos(c, permitidos)
    requeridos(c, ("nombre", "objetivo_estrategico"))
    nombre = texto(c["nombre"], "nombre")
    objetivo = texto(c["objetivo_estrategico"], "objetivo_estrategico", maximo=1000)
    descripcion = texto(c["descripcion"], "descripcion", maximo=2000, obligatorio=False) if "descripcion" in c else None
    presupuesto = dinero(c.get("presupuesto_disponible", 0), "presupuesto_disponible")
    capacidad = entero(c.get("capacidad_horas_semana", 0), "capacidad_horas_semana")
    with db.conexion(tenant_de(event)) as con:
        with con.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                INSERT INTO portafolios (nombre, descripcion, objetivo_estrategico,
                                         presupuesto_disponible, capacidad_horas_semana)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (nombre) DO NOTHING RETURNING *
            """, (nombre, descripcion, objetivo, presupuesto, capacidad))
            fila = cur.fetchone()
    if fila is None:
        return error("NOMBRE_DUPLICADO", "Ya existe un portafolio con ese nombre", 409)
    return ok(fila, estado=201)
