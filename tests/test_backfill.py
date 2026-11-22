import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import backfill
from bot_lp import TZ, _normalizar


NEWS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://www.0221.com.ar/la-plata/corte-de-luz-en-gonnet-n1</loc>
    <news:news>
      <news:publication_date>2026-11-10T15:30:00-03:00</news:publication_date>
      <news:title>Corte de luz en Gonnet por un reclamo a EDELAP</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.0221.com.ar/deportes/gano-estudiantes-n2</loc>
    <news:news>
      <news:publication_date>2026-11-10T16:00:00-03:00</news:publication_date>
      <news:title>Ganó Estudiantes en el Bosque</news:title>
    </news:news>
  </url>
</urlset>
"""

MENSUAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://www.0221.com.ar/policiales/fuerte-choque-en-romero-n3</loc>
    <lastmod>2026-11-12T20:00:00Z</lastmod>
  </url>
  <url>
    <loc>https://www.0221.com.ar/la-plata/festival-de-gastronomia-n4</loc>
    <lastmod>2026-11-12T18:00:00Z</lastmod>
  </url>
</urlset>
"""

INDEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://www.0221.com.ar/sitemap/news-full/sitemap-202611.xml.gz</loc>
  </sitemap>
  <sitemap>
    <loc>https://www.0221.com.ar/sitemap/news-full/sitemap-202610.xml.gz</loc>
  </sitemap>
</sitemapindex>
"""

ARTICULO_HTML = """
<html>
  <head>
    <meta property="og:title" content="Fuerte choque en Melchor Romero dejó dos heridos"/>
    <script type="application/ld+json">
    [{"@type":"NewsArticle","headline":"Fuerte choque en Melchor Romero dejó dos heridos","datePublished":"2026-11-12T16:45:00-03:00"}]
    </script>
  </head>
  <body><h1>Fuerte choque en Melchor Romero dejó dos heridos</h1></body>
</html>
"""


class ParseTests(unittest.TestCase):
    def test_parse_fecha_arg_fin_de_dia(self):
        dt = backfill.parse_fecha_arg("2026-11-22", fin_del_dia=True)
        self.assertEqual(dt.tzinfo, TZ)
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M:%S"), "2026-11-22 23:59:59")

    def test_parse_sitemap_news_incluye_titulo_y_fecha(self):
        filas = backfill.parse_sitemap_urlset(NEWS_XML)
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]["titulo"], "Corte de luz en Gonnet por un reclamo a EDELAP")
        self.assertEqual(filas[0]["publicado"].strftime("%Y-%m-%d %H:%M"), "2026-11-10 15:30")

    def test_urls_sitemap_mensual_filtra_mes(self):
        locs = backfill.urls_sitemap_mensual(INDEX_XML, ["202611"])
        self.assertEqual(locs, ["https://www.0221.com.ar/sitemap/news-full/sitemap-202611.xml.gz"])

    def test_extraer_articulo(self):
        titulo, fecha = backfill.extraer_articulo(ARTICULO_HTML)
        self.assertEqual(titulo, "Fuerte choque en Melchor Romero dejó dos heridos")
        self.assertEqual(fecha.strftime("%Y-%m-%d %H:%M"), "2026-11-12 16:45")

    def test_prefiltra_por_seccion_y_palabras(self):
        items = backfill.parse_sitemap_urlset(NEWS_XML + "")  # noqa
        # rebuild
        items = backfill.parse_sitemap_urlset(NEWS_XML)
        secs = {"la-plata", "policiales"}
        ok = [i for i in items if backfill.prefiltra(i, secs)]
        self.assertEqual(len(ok), 1)
        self.assertIn("gonnet", ok[0]["url"])

    def test_prefiltra_usa_slug_si_no_hay_titulo(self):
        item = {"url": "https://www.0221.com.ar/policiales/fuerte-choque-en-romero-n3", "titulo": ""}
        self.assertTrue(backfill.prefiltra(item, {"policiales"}))
        item2 = {"url": "https://www.0221.com.ar/la-plata/festival-de-gastronomia-n4", "titulo": ""}
        self.assertFalse(backfill.prefiltra(item2, {"la-plata"}))


class CandidatosTests(unittest.TestCase):
    def test_dedup_y_ventana(self):
        desde = datetime(2026, 11, 4, 22, 16, tzinfo=TZ)
        hasta = datetime(2026, 11, 22, 23, 59, tzinfo=TZ)
        items = [
            {
                "url": "https://www.0221.com.ar/la-plata/a-n1",
                "titulo": "Corte de luz en Gonnet por un reclamo a EDELAP",
                "publicado": datetime(2026, 11, 10, 15, 30, tzinfo=TZ),
                "lastmod": None,
            },
            {
                "url": "https://www.0221.com.ar/la-plata/a-n1b",
                "titulo": "Corte de luz en Gonnet por un reclamo a EDELAP",
                "publicado": datetime(2026, 11, 10, 18, 0, tzinfo=TZ),
                "lastmod": None,
            },
            {
                "url": "https://www.0221.com.ar/la-plata/viejo-n0",
                "titulo": "Un incendio destruyó una casa en Abasto",
                "publicado": datetime(2026, 11, 1, 10, 0, tzinfo=TZ),
                "lastmod": None,
            },
        ]
        vistos = {_normalizar("Taxistas, remiseros y transportistas se movilizaron y generaron un caos de tránsito en La Plata")}
        nuevas, fuera, dup = backfill.candidatos_a_filas(items, desde, hasta, vistos)
        self.assertEqual(fuera, 1)
        self.assertEqual(dup, 1)
        self.assertEqual(len(nuevas), 1)
        self.assertEqual(nuevas[0]["fecha"], "2026-11-10 15:30")
        self.assertIn("luz", nuevas[0]["palabras"])

    def test_normalizar_historial_migra_fila_vieja_con_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "historial_lp.csv"
            path.write_text(
                "fecha,evento\n"
                "2026-11-05 01:16,Un feroz incendio arrasó con una pensión\n"
                "2026-11-22 10:39,Accidente en La Plata,https://www.0221.com.ar/nota,chocó\n",
                encoding="utf-8",
            )
            filas = backfill.normalizar_historial(path)
            self.assertEqual(filas[0]["url"], "")
            self.assertEqual(filas[1]["url"], "https://www.0221.com.ar/nota")
            self.assertEqual(filas[1]["palabras"], "chocó")


class SinTelegramTests(unittest.TestCase):
    def test_main_no_dispara_telegram(self):
        with patch("backfill.recolectar_sitemaps", return_value=[]), patch(
            "bot_lp.notificar_eventos"
        ) as ev, patch("bot_lp.notificar_error") as err:
            backfill.main(["--desde", "2026-11-04 22:16", "--hasta", "2026-11-22", "--dry-run"])
            ev.assert_not_called()
            err.assert_not_called()


if __name__ == "__main__":
    unittest.main()
