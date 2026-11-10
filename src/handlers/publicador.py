"""Reenvío periódico de eventos transaccionales pendientes."""
import logging
from src import db
from src.eventos import publicar_pendientes

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def manejar(event, context):
    total, errores = 0, 0
    for tenant in db.SCHEMAS:
        try:
            total += publicar_pendientes(tenant, limite=100)
        except Exception:
            errores += 1
            log.exception("No se pudo publicar outbox para un tenant")
    if errores:
        # Fallar cron permite observar el error mediante Lambda Errors/CloudWatch.
        raise RuntimeError("Fallo reintento outbox para uno o más tenants")
    return {"publicados": total}
