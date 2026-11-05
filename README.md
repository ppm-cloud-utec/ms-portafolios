# ms-portafolios

Portafolios, proyectos, admisión de demanda y priorización. **Es el corazón del
dominio PPM:** la capa que está encima de los proyectos, donde se compara, se
prioriza y se reparte plata y gente.

- **Responsable:** @Brisseth-raton
- **Base de datos:** RDS PostgreSQL · database `ppm_portafolios` · un schema por tenant
- **Runtime:** Python 3.12 · Serverless Framework · us-east-1

## Endpoints (13, todos protegidos)

### Portafolios
| Método | Ruta | Rol | Qué hace |
|---|---|---|---|
| GET | `/portafolios` | todos | lista con el resumen de sus proyectos |
| GET | `/portafolios/{id}` | todos | detalle con sus proyectos |
| POST | `/portafolios` | pmo, admin | crea |

### Proyectos
| Método | Ruta | Rol | Qué hace |
|---|---|---|---|
| GET | `/proyectos` | todos | filtrable por `?estado=`, `?portafolio_id=`, `?salud=`, `?en_riesgo=true`, paginado |
| GET | `/proyectos/{id}` | todos | detalle con su historial de estado |
| POST | `/proyectos` | pmo, pm, admin | crea · **publica `proyecto.creado`** |
| PUT | `/proyectos/{id}` | pmo, pm, admin | cambia estado/prioridad · **publica `proyecto.priorizado`** |

### Admisión de demanda
| Método | Ruta | Rol | Qué hace |
|---|---|---|---|
| GET | `/propuestas` | todos | la bandeja, rankeada por score |
| POST | `/propuestas` | todos | cualquiera propone un proyecto |
| POST | `/propuestas/{id}/aprobar` | pmo, admin | la convierte en proyecto · **publica `proyecto.creado`** |
| POST | `/propuestas/{id}/rechazar` | pmo, admin | requiere motivo |
| GET | `/priorizacion` | todos | ranking + cuánto consume la selección y si excede |
| GET | `/health` | todos | verifica el servicio |

## Lo que hace el worker (EDA)

La cola `ppm-cola-portafolios` está suscrita al topic compartido con un filtro
de 3 tipos de evento:

| Evento | De quién | Qué hace el worker |
|---|---|---|
| `tarea.creada` | ms-tareas | recalcula `avance_pct` |
| `tarea.completada` | ms-tareas | recalcula `avance_pct` y la `salud` |
| `presupuesto.excedido` | ms-presupuestos | marca `en_riesgo` y pone la salud en rojo |

**`avance_pct`, `salud` y `en_riesgo` no se escriben nunca por HTTP.** Ese es el
punto: nadie reporta avance a mano, y este microservicio no conoce a los otros
dos. La cola tiene DLQ con `maxReceiveCount: 3`, y el worker es idempotente
(tabla `eventos_procesados`) porque SQS entrega at-least-once.

## Desplegar

`infra-compartida` tiene que estar arriba primero.

```bash
npm install            # instala Serverless v3 y el plugin de Python
npx serverless deploy
```

Necesita **Docker corriendo** si este microservicio usa RDS: el plugin compila
`psycopg` contra Amazon Linux dentro de un contenedor.

## Cargar datos

```bash
export DB_HOST=<endpoint del RDS> DB_USER=ppm_portafolios DB_PASSWORD=<de SSM>
export DB_NAME=ppm_portafolios
python seed/generar.py --esquema   # crea las tablas en los 3 schemas
python seed/generar.py             # 30,000 registros
python seed/generar.py --contar
```

## Probar

```bash
curl -i $URL/proyectos                                    # 401 sin token
curl -s "$URL/proyectos?estado=en_curso&limite=5" -H "Authorization: Bearer $TOKEN" | jq
curl -s "$URL/priorizacion?seleccion=1,5,9" -H "Authorization: Bearer $TOKEN" | jq .meta
```

## Documentación

- [Modelo de datos](docs/modelo.md) — ER, score de prioridad, volumen
- [Arquitectura general](https://github.com/ppm-cloud-utec/infra-compartida/blob/main/docs/arquitectura.md)
