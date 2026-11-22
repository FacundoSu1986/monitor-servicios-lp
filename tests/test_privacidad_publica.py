import json
import tempfile
import unittest
from pathlib import Path

from enriquecer import escribir_geojson, nota_a_feature


def nota_min():
    return {
        "id": "1",
        "url": "https://www.0221.com.ar/la-plata/identificaron-a-juan-perez-n1",
        "titulo": "Identificaron a Juan Pérez tras el choque en 7 y 50",
        "fecha_pub": "2026-11-10 15:00",
        "fecha_hecho": "2026-11-10 15:00",
        "fecha_hecho_es_publicacion": False,
        "tipo_nota": "hecho",
        "categoria": "accidente_vial",
        "gravedad": "heridos",
        "lugar_texto": "7 y 50",
        "precision": "esquina",
        "lat": -34.9148,
        "lon": -57.9475,
    }


class PrivacidadPublicaTests(unittest.TestCase):
    def test_nota_publica_omite_titulo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.geojson"
            escribir_geojson(path, [nota_min()], omitir_titulo=True)
            data = json.loads(path.read_text(encoding="utf-8"))
            props = data["features"][0]["properties"]
        self.assertNotIn("titulo", props)
        self.assertNotIn("Juan Pérez", json.dumps(data, ensure_ascii=False))
        self.assertEqual(props["url"], nota_min()["url"])
        self.assertEqual(props["lugar_texto"], "7 y 50")

    def test_nota_temporal_puede_conservar_titulo(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.geojson"
            escribir_geojson(path, [nota_min()], omitir_titulo=False)
            props = json.loads(path.read_text(encoding="utf-8"))["features"][0]["properties"]
        self.assertEqual(props["titulo"], nota_min()["titulo"])

    def test_feature_sin_titulo_si_se_pide(self):
        props = nota_a_feature(nota_min(), omitir_titulo=True)["properties"]
        self.assertNotIn("titulo", props)
        self.assertIn("url", props)
