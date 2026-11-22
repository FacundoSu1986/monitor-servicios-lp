import csv
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot_lp import extraer_titulares, palabras_encontradas, leer_eventos_previos, _normalizar


HTML = """
<html>
  <article>
    <h2 class="news-article__title"><a href="/nota/corte-luz">Corte de luz en Gonnet por un reclamo a EDELAP</a></h2>
  </article>
  <article>
    <h2 class="news-article__title">Salen a la luz las primeras pericias que explican cómo fue la muerte</h2>
  </article>
  <article>
    <h2 class="news-article__title">Así fue el brutal accidente en que un motociclista chocó contra un auto estacionado en La Plata</h2>
  </article>
  <article>
    <h2 class="news-article__title">El Mercado Bonaerense renovó sus ofertas en carnes y verduras</h2>
  </article>
  <article>
    <h2 class="news-article__title">Corte de luz en Gonnet por un reclamo a EDELAP</h2>
  </article>
</html>
"""


class PalabrasTests(unittest.TestCase):
    def test_corte_de_luz_matchea(self):
        self.assertIn("luz", palabras_encontradas("Corte de luz en Gonnet por un reclamo a EDELAP"))

    def test_a_la_luz_no_matchea(self):
        self.assertEqual(palabras_encontradas("Salen a la luz las primeras pericias"), [])

    def test_choco_matchea(self):
        self.assertIn("chocó", palabras_encontradas("un motociclista chocó contra un auto"))

    def test_sin_coincidencia(self):
        self.assertEqual(palabras_encontradas("Festival de la Gastronomía Italiana en La Plata"), [])


class ExtraerTests(unittest.TestCase):
    def test_extrae_solo_alertas_y_dedup_en_pagina(self):
        filas = extraer_titulares(HTML, "https://www.0221.com.ar/la-plata")
        eventos = [f["evento"] for f in filas]
        self.assertEqual(len(eventos), 2)
        self.assertTrue(any("Corte de luz" in e for e in eventos))
        self.assertTrue(any("chocó" in e for e in eventos))
        self.assertFalse(any("pericias" in e for e in eventos))

    def test_guarda_url_absoluta(self):
        filas = extraer_titulares(HTML, "https://www.0221.com.ar/la-plata")
        corte = next(f for f in filas if "Corte de luz" in f["evento"])
        self.assertEqual(corte["url"], "https://www.0221.com.ar/nota/corte-luz")


class HistorialTests(unittest.TestCase):
    def test_dedup_contra_csv(self, tmp_path=None):
        dest = Path(self.id().replace(".", "_") + ".csv")
        self.addCleanup(lambda: dest.exists() and dest.unlink())
        dest.write_text(
            "fecha,evento,url,palabras\n2026-01-06 18:53,Corte de luz en Gonnet por un reclamo a EDELAP,https://x,luz\n",
            encoding="utf-8",
        )
        vistos = leer_eventos_previos(dest)
        self.assertIn(_normalizar("Corte de luz en Gonnet por un reclamo a EDELAP"), vistos)
        filas = extraer_titulares(HTML, "https://www.0221.com.ar/la-plata")
        nuevos = [f for f in filas if _normalizar(f["evento"]) not in vistos]
        self.assertEqual(len(nuevos), 1)
        self.assertIn("chocó", nuevos[0]["evento"])


if __name__ == "__main__":
    unittest.main()
