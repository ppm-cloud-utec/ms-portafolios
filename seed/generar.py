#!/usr/bin/env python3
"""Carga 30,000 registros en ms-portafolios, repartidos en 3 tenants.

    python seed/generar.py --esquema   # aplica esquema.sql en los 3 schemas
    python seed/generar.py             # carga los datos
    python seed/generar.py --contar    # cuenta lo que hay

Reparto por tenant (10,000 registros):
    portafolios         20
    propuestas         300
    proyectos          500
    historial_estado  9,180
                    ------
                    10,000

Los 3 tenants tienen PERFILES DISTINTOS a proposito: Acme son obras caras con
pocos proyectos, Globex consultorias cortas y numerosas, Initech desarrollos de
producto. Si los datos salieran iguales, el multi-tenancy se veria falso.
"""
import os
import sys
import random
import argparse
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg

SCHEMAS = {"t-acme": "t_acme", "t-globex": "t_globex", "t-initech": "t_initech"}

PERFILES = {
    "t-acme": dict(
        nombre="Acme Ingenieria",
        portafolios=["Obras Lima Norte", "Obras Lima Sur", "Infraestructura Vial",
                     "Edificaciones Corporativas", "Saneamiento"],
        n_portafolios=20, n_propuestas=300, n_proyectos=500,
        presupuesto=(200_000, 900_000),     # obras caras
        tareas_tipicas=(8, 20),
        objetos=["Edificio", "Puente", "Via", "Planta", "Reservorio", "Colegio",
                 "Hospital", "Mercado", "Parque", "Canal"],
        lugares=["Comas", "Villa El Salvador", "Ate", "Callao", "Chorrillos",
                 "San Juan de Lurigancho", "Carabayllo", "Lurin"],
    ),
    "t-globex": dict(
        nombre="Globex Consulting",
        portafolios=["Cartera Banca", "Cartera Retail", "Cartera Minería",
                     "Cartera Sector Publico", "Transformacion Digital"],
        n_portafolios=20, n_propuestas=300, n_proyectos=500,
        presupuesto=(20_000, 150_000),      # consultorias cortas
        tareas_tipicas=(20, 60),
        objetos=["Diagnostico", "Implementacion", "Migracion", "Auditoria",
                 "Rediseño", "Automatizacion", "Integracion", "Capacitacion"],
        lugares=["BCP", "Interbank", "Falabella", "Backus", "Antamina",
                 "Alicorp", "Rimac", "Claro", "SUNAT", "MINEDU"],
    ),
    "t-initech": dict(
        nombre="Initech Software",
        portafolios=["Plataforma Core", "App Movil", "Analitica", "Integraciones",
                     "Infraestructura"],
        n_portafolios=20, n_propuestas=300, n_proyectos=500,
        presupuesto=(10_000, 60_000),       # desarrollos de producto
        tareas_tipicas=(150, 500),
        objetos=["Modulo", "API", "Rediseño", "Refactor", "Motor", "Panel",
                 "Servicio", "Conector", "SDK", "Pipeline"],
        lugares=["Facturacion", "Pagos", "Usuarios", "Reportes", "Notificaciones",
                 "Busqueda", "Permisos", "Auditoria", "Catalogo"],
    ),
}

ESTADOS = ["propuesto", "aprobado", "en_curso", "en_pausa", "cerrado", "cancelado"]
PESO_ESTADOS = [10, 15, 35, 8, 28, 4]   # la mayoria en curso o cerrado
OBJETIVOS = [
    "Crecer en participacion de mercado", "Reducir costo operativo",
    "Cumplir normativa", "Mejorar experiencia del cliente",
    "Modernizar la plataforma tecnologica", "Expandirse a nuevas regiones",
]


def dsn(db_name=None):
    """Parámetros explícitos: credenciales con caracteres especiales no rompen DSN."""
    return dict(host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", 5432)),
                dbname=db_name or os.environ.get("DB_NAME", "ppm_portafolios"),
                user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
                connect_timeout=10, sslmode=os.environ.get("DB_SSLMODE", "require"))


def aplicar_esquema():
    """Corre esquema.sql en los 3 schemas de tenant."""
    sql = open(os.path.join(os.path.dirname(__file__), "esquema.sql")).read()
    with psycopg.connect(**dsn()) as con:
        for tenant_id, schema in SCHEMAS.items():
            with con.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                cur.execute("SELECT set_config('search_path', %s, true)", (schema,))
                cur.execute(sql)
            con.commit()
            print(f"  esquema aplicado en {schema}")


def generar(tenant_id, perfil):
    schema = SCHEMAS[tenant_id]
    hoy = date.today()
    total = 0

    with psycopg.connect(**dsn()) as con:
        with con.cursor() as cur:
            cur.execute("SELECT set_config('search_path', %s, true)", (schema,))

            # Nunca mezclar una nueva carga con datos previos.
            cur.execute("""SELECT
                (SELECT COUNT(*) FROM portafolios) +
                (SELECT COUNT(*) FROM proyectos) +
                (SELECT COUNT(*) FROM propuestas) +
                (SELECT COUNT(*) FROM historial_estado)""")
            if cur.fetchone()[0]:
                raise RuntimeError(f"{schema} ya contiene datos; el seed solo se ejecuta una vez")

            # --- portafolios ---
            ids_portafolio = []
            for i in range(perfil["n_portafolios"]):
                base = perfil["portafolios"][i % len(perfil["portafolios"])]
                nombre = f"{base} {2024 + i // len(perfil['portafolios'])}-{i:02d}"
                cur.execute("""
                    INSERT INTO portafolios
                        (nombre, descripcion, objetivo_estrategico,
                         presupuesto_disponible, capacidad_horas_semana)
                    VALUES (%s,%s,%s,%s,%s) RETURNING id
                """, (nombre, f"Portafolio de {perfil['nombre']}",
                      random.choice(OBJETIVOS),
                      random.randint(*perfil["presupuesto"]) * random.randint(4, 12),
                      random.choice([160, 240, 320, 400, 600])))
                ids_portafolio.append(cur.fetchone()[0])
                total += 1
            print(f"    {len(ids_portafolio)} portafolios")

            def titulo():
                return (f"{random.choice(perfil['objetos'])} "
                        f"{random.choice(perfil['lugares'])}")

            # --- proyectos ---
            ids_proyecto = []
            for i in range(perfil["n_proyectos"]):
                estado = random.choices(ESTADOS, weights=PESO_ESTADOS)[0]
                inicio = hoy - timedelta(days=random.randint(0, 540))
                presupuesto = random.randint(*perfil["presupuesto"])
                avance = {
                    "propuesto": 0, "aprobado": 0,
                    "en_curso": random.randint(5, 95),
                    "en_pausa": random.randint(10, 70),
                    "cerrado": 100, "cancelado": random.randint(5, 60),
                }[estado]
                en_riesgo = random.random() < 0.12
                salud = ("rojo" if en_riesgo
                         else "ambar" if estado == "en_curso" and avance < 50
                         else "verde")
                cur.execute("""
                    INSERT INTO proyectos
                        (portafolio_id, codigo, nombre, descripcion, estado,
                         prioridad_score, fecha_inicio, fecha_fin_estimada,
                         presupuesto_estimado, avance_pct, salud, en_riesgo,
                         creado_en)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                """, (random.choice(ids_portafolio),
                      f"PRY-{tenant_id[2:5].upper()}-{i:05d}",
                      titulo(), f"Proyecto de {perfil['nombre']}", estado,
                      round(random.uniform(15, 95), 2), inicio,
                      inicio + timedelta(days=random.randint(45, 540)),
                      presupuesto, avance, salud, en_riesgo, inicio))
                ids_proyecto.append((cur.fetchone()[0], estado, avance, salud))
                total += 1
            print(f"    {len(ids_proyecto)} proyectos")

            # --- propuestas ---
            for i in range(perfil["n_propuestas"]):
                costo = random.randint(*perfil["presupuesto"])
                beneficio = int(costo * random.uniform(0.6, 2.8))
                ve, ur, ri = (random.randint(1, 5), random.randint(1, 5),
                              random.randint(1, 5))
                retorno = min(beneficio / costo, 3.0)
                score = round(max(0.0, min(ve * 8 + ur * 5 + retorno * 8.33 - ri * 2, 100.0)), 2)
                estado = random.choices(["pendiente", "aprobada", "rechazada"],
                                        weights=[25, 50, 25])[0]
                cur.execute("""
                    INSERT INTO propuestas
                        (portafolio_id, nombre, problema, costo_estimado,
                         beneficio_esperado, valor_estrategico, urgencia, riesgo,
                         score, estado, creado_en)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (random.choice(ids_portafolio), titulo(),
                      "Oportunidad detectada por el area usuaria",
                      costo, beneficio, ve, ur, ri, score, estado,
                      hoy - timedelta(days=random.randint(0, 200))))
                total += 1
            print(f"    {perfil['n_propuestas']} propuestas")

            # --- historial_estado: completa hasta los 10,000 ---
            faltan = 10_000 - total
            lote = []
            for _ in range(faltan):
                pid, estado, avance, salud = random.choice(ids_proyecto)
                anterior = random.choice(ESTADOS)
                lote.append((pid, anterior, estado,
                             round(random.uniform(0, float(avance)), 2), salud,
                             random.choice(["recalculo automatico por evento",
                                            "cambio manual", "revision de portafolio",
                                            "ajuste de presupuesto"]),
                             hoy - timedelta(days=random.randint(0, 540))))
            cur.executemany("""
                INSERT INTO historial_estado
                    (proyecto_id, estado_anterior, estado_nuevo, avance_pct,
                     salud, motivo, cambiado_en)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, lote)
            total += faltan
            print(f"    {faltan} filas de historial")
        con.commit()
    return total


def contar():
    print(f"Database: {os.environ.get('DB_NAME', 'ppm_portafolios')}\n")
    gran_total = 0
    for tenant_id, schema in SCHEMAS.items():
        with psycopg.connect(**dsn()) as con, con.cursor() as cur:
            cur.execute("SELECT set_config('search_path', %s, true)", (schema,))
            sub = 0
            print(f"  {schema}")
            for t in ("portafolios", "proyectos", "propuestas", "historial_estado"):
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                n = cur.fetchone()[0]
                print(f"    {t:<20} {n:>7}")
                sub += n
            print(f"    {'subtotal':<20} {sub:>7}\n")
            gran_total += sub
    print(f"  TOTAL {gran_total:>24}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--esquema", action="store_true")
    ap.add_argument("--contar", action="store_true")
    ap.add_argument("--tenant", choices=list(SCHEMAS))
    args = ap.parse_args()

    for v in ("DB_HOST", "DB_USER", "DB_PASSWORD"):
        if not os.environ.get(v):
            sys.exit(f"Falta la variable de entorno {v}")

    if args.esquema:
        return aplicar_esquema()
    if args.contar:
        return contar()

    random.seed(2026)   # reproducible
    objetivo = {args.tenant: PERFILES[args.tenant]} if args.tenant else PERFILES
    total = 0
    for tenant_id, perfil in objetivo.items():
        print(f"== {perfil['nombre']} ({tenant_id}) ==")
        n = generar(tenant_id, perfil)
        print(f"   {n} registros\n")
        total += n
    print(f"Listo: {total} registros. Verifica con --contar")


if __name__ == "__main__":
    main()
