"""Aprobar una propuesta exige presupuesto en el portafolio (sin AWS)."""
import json
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.handlers import propuestas


def evento():
    return {"requestContext": {"authorizer": {"tenant_id": "t-acme", "rol": "pmo",
                                              "usuario_id": str(uuid4())}},
            "pathParameters": {"id": "7"}, "body": json.dumps({})}


def con_bd(filas):
    """Conexion falsa: cada fetchone devuelve la siguiente fila de la lista."""
    cur = MagicMock()
    cur.fetchone.side_effect = filas
    cur.__enter__.return_value = cur
    con = MagicMock()
    con.cursor.return_value = cur

    @contextmanager
    def conexion(_tenant):
        yield con
    return conexion, cur


def aprobar(filas):
    conexion, cur = con_bd(filas)
    with patch.object(propuestas.db, "conexion", conexion), \
         patch("src.tenants.habilitados", return_value={"t-acme"}), \
         patch.object(propuestas, "encolar", return_value="e1"), \
         patch.object(propuestas, "intentar_publicar"):
        return propuestas.aprobar(evento(), None), cur


PROPUESTA = {"id": 7, "estado": "pendiente", "portafolio_id": 1, "nombre": "X",
             "problema": "p", "score": Decimal("3"), "costo_estimado": Decimal("50000")}


def test_rechaza_si_supera_la_holgura():
    r, cur = aprobar([PROPUESTA, {"activo": True, "presupuesto_disponible": Decimal("100000")},
                      {"comprometido": Decimal("80000")}])
    assert r["statusCode"] == 409, r
    assert json.loads(r["body"])["error"]["codigo"] == "PRESUPUESTO_INSUFICIENTE"
    assert not any("INSERT INTO proyectos" in str(c) for c in cur.execute.call_args_list)


def test_rechaza_portafolio_inactivo():
    r, _ = aprobar([PROPUESTA, {"activo": False, "presupuesto_disponible": Decimal("1e9")}])
    assert r["statusCode"] == 409
    assert json.loads(r["body"])["error"]["codigo"] == "PORTAFOLIO_INVALIDO"


def test_aprueba_si_alcanza():
    proyecto = {"id": 99, "codigo": "PRY-1", "nombre": "X", "portafolio_id": 1,
                "presupuesto_estimado": Decimal("50000"), "responsable_id": None}
    r, _ = aprobar([PROPUESTA, {"activo": True, "presupuesto_disponible": Decimal("100000")},
                    {"comprometido": Decimal("50000")}, proyecto])
    assert r["statusCode"] == 201, r


if __name__ == "__main__":
    for t in (test_rechaza_si_supera_la_holgura, test_rechaza_portafolio_inactivo, test_aprueba_si_alcanza):
        t(); print("[OK]", t.__name__)
