"""Manejo centralizado de errores y logging sin filtrar SQL ni secretos."""
import functools
import logging

from psycopg import Error as PostgresError
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation

from src.respuesta import error
from src.validacion import ValidacionError

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def endpoint(handler):
    @functools.wraps(handler)
    def envuelto(event, context):
        try:
            return handler(event, context)
        except ValidacionError as exc:
            return error("ENTRADA_INVALIDA", str(exc), 400)
        except PermissionError:
            return error("NO_AUTORIZADO", "No se pudo validar la sesión", 401)
        except UniqueViolation:
            return error("DUPLICADO", "El recurso ya existe", 409)
        except (ForeignKeyViolation, CheckViolation):
            return error("CONFLICTO_DATOS", "La operación viola una restricción de datos", 409)
        except PostgresError:
            log.exception("Error de base de datos; request_id=%s", getattr(context, "aws_request_id", "local"))
            return error("SERVICIO_NO_DISPONIBLE", "No fue posible completar la operación", 503)
        except Exception:
            log.exception("Error inesperado; request_id=%s", getattr(context, "aws_request_id", "local"))
            return error("ERROR_INTERNO", "No fue posible completar la operación", 500)
    return envuelto
