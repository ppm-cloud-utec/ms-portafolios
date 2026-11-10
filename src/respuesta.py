"""Contrato JSON de respuesta y contexto de seguridad de API Gateway REST."""
import base64
import binascii
import json
import os
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from src.validacion import ValidacionError


def _cabeceras():
    origen = os.getenv("CORS_ALLOWED_ORIGIN", "*")
    return {
        "Access-Control-Allow-Origin": origen,
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Vary": "Origin",
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }


class _Codificador(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o) if o % 1 else int(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        return super().default(o)


def ok(datos, meta=None, estado=200):
    cuerpo = {"ok": True, "datos": datos}
    if meta is not None:
        cuerpo["meta"] = meta
    return {"statusCode": estado, "headers": _cabeceras(),
            "body": json.dumps(cuerpo, cls=_Codificador, ensure_ascii=False, allow_nan=False)}


def error(codigo, mensaje, estado=400):
    return {"statusCode": estado, "headers": _cabeceras(),
            "body": json.dumps({"ok": False, "error": {"codigo": codigo, "mensaje": mensaje}},
                               ensure_ascii=False)}


def _autorizador(event):
    ctx = (event.get("requestContext") or {}).get("authorizer") or {}
    if not isinstance(ctx, dict):
        raise PermissionError("Sin contexto de autorización")
    return ctx


def tenant_de(event):
    from src.db import SCHEMAS
    tenant = _autorizador(event).get("tenant_id")
    if tenant not in SCHEMAS:
        raise PermissionError("Tenant no autorizado")
    return tenant


def usuario_de(event):
    from uuid import UUID
    usuario = _autorizador(event).get("usuario_id")
    try:
        return str(UUID(str(usuario)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PermissionError("Identidad no válida") from exc


def rol_de(event):
    rol = _autorizador(event).get("rol")
    if rol not in ("admin", "pmo", "pm", "miembro"):
        raise PermissionError("Rol inválido")
    return rol


def cuerpo_de(event):
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        try:
            body = base64.b64decode(body, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValidacionError("El cuerpo no contiene base64 válido") from exc
    if len(body if isinstance(body, bytes) else body.encode("utf-8")) > 16 * 1024:
        raise ValidacionError("El cuerpo supera el límite de 16 KiB")
    try:
        obj = json.loads(body)
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValidacionError("El cuerpo debe contener JSON válido") from exc
    if not isinstance(obj, dict):
        raise ValidacionError("El cuerpo debe ser un objeto JSON")
    return obj
