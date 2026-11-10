"""Autorización RBAC. Nunca aceptar roles desde el body HTTP."""
from src.respuesta import error, rol_de

ADMIN = "admin"
PMO = "pmo"
PM = "pm"
MIEMBRO = "miembro"
TODOS = (ADMIN, PMO, PM, MIEMBRO)


def exige(event, *permitidos):
    rol = rol_de(event)
    if rol not in permitidos:
        return error("ROL_INSUFICIENTE", "No tienes permisos para esta operación", 403)
    return None
