-- Esquema de ms-portafolios. Se aplica IGUAL en los 3 schemas de tenant
-- (t_acme, t_globex, t_initech) dentro de la database ppm_portafolios.
--
-- Patron del enunciado: Single Database, Multiple Schemas. Las 3 copias son
-- identicas; lo que cambia son los datos.

-- ---------------------------------------------------------------- portafolios
-- El nivel que esta ENCIMA de los proyectos: donde se compara, se prioriza y
-- se reparte plata y gente. Es la capa que hace de esto un PPM y no un gestor
-- de tareas.
CREATE TABLE IF NOT EXISTS portafolios (
    id                      BIGSERIAL PRIMARY KEY,
    nombre                  TEXT        NOT NULL,
    descripcion             TEXT,
    objetivo_estrategico    TEXT        NOT NULL,
    presupuesto_disponible  NUMERIC(14,2) NOT NULL DEFAULT 0,
    capacidad_horas_semana  INTEGER     NOT NULL DEFAULT 0,
    activo                  BOOLEAN     NOT NULL DEFAULT TRUE,
    creado_en               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (nombre)
);

-- -------------------------------------------------------------------- estados
CREATE TABLE IF NOT EXISTS estados (
    codigo      TEXT PRIMARY KEY,
    descripcion TEXT NOT NULL,
    orden       INTEGER NOT NULL
);

INSERT INTO estados (codigo, descripcion, orden) VALUES
    ('propuesto', 'Esperando decision del PMO',        1),
    ('aprobado',  'Aprobado, aun no arranca',          2),
    ('en_curso',  'En ejecucion',                      3),
    ('en_pausa',  'Detenido temporalmente',            4),
    ('cerrado',   'Terminado',                         5),
    ('cancelado', 'Descartado antes de terminar',      6)
ON CONFLICT (codigo) DO NOTHING;

-- ------------------------------------------------------------------ proyectos
CREATE TABLE IF NOT EXISTS proyectos (
    id                      BIGSERIAL PRIMARY KEY,
    portafolio_id           BIGINT      NOT NULL REFERENCES portafolios(id) ON DELETE RESTRICT,
    codigo                  TEXT        NOT NULL,
    nombre                  TEXT        NOT NULL,
    descripcion             TEXT,
    estado                  TEXT        NOT NULL DEFAULT 'propuesto' REFERENCES estados(codigo),
    prioridad_score         NUMERIC(5,2) NOT NULL DEFAULT 0,
    -- responsable_id es un identificador LOGICO del usuario en ms-identidad.
    -- No hay FK ni validacion HTTP: los microservicios son autonomos.
    responsable_id          UUID,
    fecha_inicio            DATE,
    fecha_fin_estimada      DATE,
    presupuesto_estimado    NUMERIC(14,2) NOT NULL DEFAULT 0,
    -- Estos 3 los mantienen los WORKERS a partir de eventos, no la API:
    avance_pct              NUMERIC(5,2) NOT NULL DEFAULT 0,   -- de ms-tareas
    salud                   TEXT        NOT NULL DEFAULT 'verde',
    en_riesgo               BOOLEAN     NOT NULL DEFAULT FALSE, -- de ms-presupuestos
    creado_en               TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (codigo),
    CHECK (salud IN ('verde', 'ambar', 'rojo')),
    CHECK (avance_pct BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS ix_proyectos_portafolio ON proyectos (portafolio_id);
CREATE INDEX IF NOT EXISTS ix_proyectos_estado     ON proyectos (estado);
CREATE INDEX IF NOT EXISTS ix_proyectos_prioridad  ON proyectos (prioridad_score DESC);

-- ------------------------------------------------------------------ propuestas
-- La admision de demanda (intake): cualquiera propone, el PMO decide. Es la
-- puerta de entrada del portafolio.
CREATE TABLE IF NOT EXISTS propuestas (
    id                  BIGSERIAL PRIMARY KEY,
    portafolio_id       BIGINT      NOT NULL REFERENCES portafolios(id) ON DELETE RESTRICT,
    nombre              TEXT        NOT NULL,
    problema            TEXT        NOT NULL,
    costo_estimado      NUMERIC(14,2) NOT NULL,
    beneficio_esperado  NUMERIC(14,2) NOT NULL,
    -- Los 3 criterios del score, en escala 1-5
    valor_estrategico   SMALLINT    NOT NULL,
    urgencia            SMALLINT    NOT NULL,
    riesgo              SMALLINT    NOT NULL,
    score               NUMERIC(5,2) NOT NULL DEFAULT 0,
    estado              TEXT        NOT NULL DEFAULT 'pendiente',
    solicitante_id      UUID,
    decidido_por        UUID,
    decidido_en         TIMESTAMPTZ,
    motivo_decision     TEXT,
    proyecto_id         BIGINT      REFERENCES proyectos(id),  -- si se aprobo
    creado_en           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (estado IN ('pendiente', 'aprobada', 'rechazada')),
    CHECK (valor_estrategico BETWEEN 1 AND 5),
    CHECK (urgencia BETWEEN 1 AND 5),
    CHECK (riesgo BETWEEN 1 AND 5)
);

CREATE INDEX IF NOT EXISTS ix_propuestas_estado ON propuestas (estado, score DESC);

-- ------------------------------------------------------------ historial_estado
-- Auditoria de los cambios de estado. Es la tabla que aporta el volumen y la
-- que permite mostrar la evolucion del portafolio en el tiempo.
CREATE TABLE IF NOT EXISTS historial_estado (
    id              BIGSERIAL PRIMARY KEY,
    proyecto_id     BIGINT      NOT NULL REFERENCES proyectos(id) ON DELETE CASCADE,
    estado_anterior TEXT,
    estado_nuevo    TEXT        NOT NULL,
    avance_pct      NUMERIC(5,2),
    salud           TEXT,
    motivo          TEXT,
    cambiado_por    UUID,
    cambiado_en     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_historial_proyecto ON historial_estado (proyecto_id, cambiado_en DESC);

-- --------------------------------------------------------- eventos_procesados
-- Idempotencia del worker. SQS entrega at-least-once: sin esto, un evento
-- repetido infla el avance del proyecto.
CREATE TABLE IF NOT EXISTS eventos_procesados (
    evento_id    UUID PRIMARY KEY,
    tipo         TEXT NOT NULL,
    procesado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
