# ms-portafolios

Portafolios, proyectos, propuestas y priorizacion de la cartera.

Parte del proyecto final de Cloud Computing (CS2032), Grupo 4. Todas las rutas cuelgan de la API compartida `ppm-api-dev` bajo `/portafolios` y, salvo el login, piden el token JWT.

Base de datos: RDS PostgreSQL, base ppm_portafolios, un schema por organizacion.

## Endpoints

- GET /portafolios/health
- GET /portafolios/portafolios
- GET /portafolios/portafolios/{id}
- POST /portafolios/portafolios
- GET /portafolios/proyectos
- GET /portafolios/proyectos/{id}
- POST /portafolios/proyectos
- PUT /portafolios/proyectos/{id}
- GET /portafolios/propuestas
- POST /portafolios/propuestas
- POST /portafolios/propuestas/{id}/aprobar
- POST /portafolios/propuestas/{id}/rechazar
- GET /portafolios/priorizacion

## Eventos

Publica proyecto.creado y proyecto.priorizado usando una tabla outbox que se reenvia cada minuto con EventBridge. Consume tarea.creada, tarea.completada y presupuesto.excedido desde su cola SQS para recalcular el avance.

## Despliegue

Requiere que `infra-compartida` ya este desplegado.

```bash
npm install
npx serverless deploy
```

Los servicios con RDS necesitan Docker para compilar psycopg.

## Datos de prueba

```bash
python seed/generar.py
python seed/generar.py --contar
```
