import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta

from bot_lp import TZ
from eventos import agrupar_eventos, construir_eventos, distancia_m, mismo_lugar


def nota(**kw):
    base = {
        "id": "1",
        "url": "https://www.0221.com.ar/x/y-n1",
        "titulo": "Choque en 7 y 50",
        "fecha_pub": "2026-11-10 15:00",
        "fecha_hecho": "2026-11-10 15:00",
        "fecha_hecho_es_publicacion": False,
        "tipo_nota": "hecho",
        "categoria": "accidente_vial",
        "gravedad": "heridos",
        "lugar_texto": "7 y 50",
        "lat": -34.9148,
        "lon": -57.9497,
        "precision": "esquina",
    }
    base.update(kw)
    return base


class DistanciaTests(unittest.TestCase):
    def test_misma_esquina(self):
        self.assertLess(distancia_m((-34.9148, -57.9497), (-34.9148, -57.9497)), 1)

    def test_300m_limite(self):
        # ~0.0027 deg lat ≈ 300 m
        a = (-34.9148, -57.9497)
        cerca = (-34.9148 + 0.0015, -57.9497)  # ~167 m
        lejos = (-34.9148 + 0.0045, -57.9497)  # ~500 m
        self.assertTrue(mismo_lugar(nota(lat=a[0], lon=a[1]), nota(id="2", lat=cerca[0], lon=cerca[1])))
        self.assertFalse(mismo_lugar(nota(lat=a[0], lon=a[1]), nota(id="2", lat=lejos[0], lon=lejos[1])))


class AgruparTests(unittest.TestCase):
    def test_mismo_hecho_en_24_horas(self):
        a = nota(id="10", fecha_hecho="2026-11-10 15:00", titulo="Choque en 7 y 50 dejó heridos")
        b = nota(id="11", fecha_hecho="2026-11-11 10:00", fecha_pub="2026-11-11 10:00",
                 titulo="Identificaron al conductor del choque en 7 y 50", gravedad="fatal")
        ev = construir_eventos([a, b])
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["n_notas"], 2)
        self.assertEqual(ev[0]["gravedad"], "fatal")
        self.assertEqual(len(ev[0]["urls"]), 2)
        self.assertEqual(ev[0]["precision"], "esquina")

    def test_otra_categoria_no_agrupa(self):
        a = nota(id="10", categoria="accidente_vial")
        b = nota(id="11", categoria="robo", titulo="Robo en 7 y 50")
        self.assertEqual(len(construir_eventos([a, b])), 2)

    def test_mas_de_48_horas_no_agrupa(self):
        a = nota(id="10", fecha_hecho="2026-11-10 15:00")
        b = nota(id="11", fecha_hecho="2026-11-12 16:00", fecha_pub="2026-11-12 16:00")
        self.assertEqual(len(construir_eventos([a, b])), 2)

    def test_mismo_barrio_sin_coords(self):
        a = nota(id="10", precision="localidad", lugar_texto="Los Hornos", lat=None, lon=None, categoria="incendio")
        b = nota(id="11", precision="localidad", lugar_texto="Los Hornos", lat=None, lon=None,
                 categoria="incendio", fecha_hecho="2026-11-10 20:00", fecha_pub="2026-11-10 20:00")
        ev = construir_eventos([a, b])
        self.assertEqual(len(ev), 1)
        self.assertIsNone(ev[0]["lat"])
        self.assertEqual(ev[0]["precision"], "localidad")

    def test_sin_lugar_no_se_fusiona(self):
        a = nota(id="10", precision="sin_lugar", lugar_texto="", lat=None, lon=None)
        b = nota(id="11", precision="sin_lugar", lugar_texto="", lat=None, lon=None,
                 fecha_hecho="2026-11-10 16:00", fecha_pub="2026-11-10 16:00")
        self.assertEqual(len(construir_eventos([a, b])), 2)

    def test_id_estable_con_la_nota_mas_antigua(self):
        b = nota(id="20", fecha_hecho="2026-11-10 18:00", fecha_pub="2026-11-10 18:00")
        a = nota(id="15", fecha_hecho="2026-11-10 15:00")
        ev = construir_eventos([b, a])
        self.assertEqual(ev[0]["id_evento"], "e15")

    def test_no_pisa_docs_fuera_del_repo(self):
        import tempfile
        import eventos
        docs = eventos.SALIDA_DOCS
        antes = docs.read_text(encoding="utf-8") if docs.is_file() else None
        with tempfile.TemporaryDirectory() as tmp:
            ev = Path(tmp) / "eventos.geojson"
            eventos.actualizar_eventos(notas=[], salida=ev)
            self.assertTrue(ev.is_file())
        despues = docs.read_text(encoding="utf-8") if docs.is_file() else None
        self.assertEqual(antes, despues)

    def test_escritura_publica_omite_titulos(self):
        import eventos
        a = nota(id="10", titulo="Identificaron a Juan Pérez tras el choque en 7 y 50")
        ev = construir_eventos([a])
        self.assertIn("titulos", ev[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eventos.geojson"
            eventos.escribir_eventos(path, ev)
            data = json.loads(path.read_text(encoding="utf-8"))
            props = data["features"][0]["properties"]
        self.assertNotIn("titulos", props)
        self.assertNotIn("titulo", props)
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("Juan Pérez", blob)
        self.assertNotIn("Identificaron", blob)
        self.assertTrue(props.get("urls"))
        self.assertEqual(props["categoria"], "accidente_vial")
        self.assertEqual(props["lugar_texto"], "7 y 50")
        self.assertIn("fecha_hecho", props)
        self.assertIn("gravedad", props)


if __name__ == "__main__":
    unittest.main()
