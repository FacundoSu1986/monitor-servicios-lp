import csv
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot_lp import (
    extraer_titulares,
    palabras_encontradas,
    leer_eventos_previos,
    _normalizar,
    armar_mensajes,
    notificar_eventos,
    notificar_error,
    scraping_la_plata,
    TELEGRAM_MAX,
    TIMEOUT,
)
import bot_lp


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

    def test_ruta_historial_usa_env(self):
        with patch.dict(os.environ, {"HISTORIAL_PATH": "/tmp/datos/historial_lp.csv"}):
            self.assertEqual(bot_lp.ruta_historial(), Path("/tmp/datos/historial_lp.csv"))

    def test_ruta_historial_default_es_archivo(self):
        env = {k: v for k, v in os.environ.items() if k != "HISTORIAL_PATH"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(bot_lp.ruta_historial(), bot_lp.ARCHIVO)
            self.assertEqual(bot_lp.ruta_historial(), Path("historial_lp.csv"))


ENV_TG = {"TELEGRAM_BOT_TOKEN": "tok-test", "TELEGRAM_CHAT_ID": "12345"}


class TelegramTests(unittest.TestCase):
    def test_sin_credenciales_no_envia(self):
        vacio = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}
        with patch.dict(os.environ, vacio, clear=False), patch("bot_lp.requests.post") as post:
            notificar_eventos([{"evento": "Corte de luz", "url": "https://ejemplo.test/1"}])
            post.assert_not_called()

    def test_falta_un_dato_no_envia(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok-test", "TELEGRAM_CHAT_ID": ""}, clear=False), patch(
            "bot_lp.requests.post"
        ) as post:
            notificar_eventos([{"evento": "Corte de luz", "url": "https://ejemplo.test/1"}])
            post.assert_not_called()

    def test_envia_titular_y_link_en_un_mensaje(self):
        mock_resp = MagicMock()
        with patch.dict(os.environ, ENV_TG, clear=False), patch("bot_lp.requests.post", return_value=mock_resp) as post:
            notificar_eventos(
                [{"evento": "Corte de luz en Gonnet", "url": "https://www.0221.com.ar/nota/1"}]
            )
            post.assert_called_once()
            url = post.call_args.args[0]
            self.assertEqual(url, "https://api.telegram.org/bottok-test/sendMessage")
            payload = post.call_args.kwargs["json"]
            self.assertEqual(payload["chat_id"], "12345")
            self.assertIn("Corte de luz en Gonnet", payload["text"])
            self.assertIn("https://www.0221.com.ar/nota/1", payload["text"])
            self.assertLessEqual(len(payload["text"]), TELEGRAM_MAX)
            self.assertEqual(post.call_args.kwargs["timeout"], TIMEOUT)
            mock_resp.raise_for_status.assert_called_once()

    def test_parte_mensajes_por_limite_4096(self):
        filas = [{"evento": "A" * 1800, "url": "https://ejemplo.test/" + "u" * 200} for _ in range(6)]
        mensajes = armar_mensajes(filas)
        self.assertGreater(len(mensajes), 1)
        for msg in mensajes:
            self.assertLessEqual(len(msg), TELEGRAM_MAX)
        with patch.dict(os.environ, ENV_TG, clear=False), patch("bot_lp.requests.post") as post:
            post.return_value = MagicMock()
            notificar_eventos(filas)
            self.assertEqual(post.call_count, len(mensajes))
            for call in post.call_args_list:
                self.assertLessEqual(len(call.kwargs["json"]["text"]), TELEGRAM_MAX)

    def test_error_de_scrape_avisa_y_relanza(self):
        sesion = MagicMock()
        sesion.get.side_effect = bot_lp.requests.ConnectionError("down")
        with patch.dict(os.environ, ENV_TG, clear=False), patch(
            "bot_lp.crear_sesion", return_value=sesion
        ), patch("bot_lp.requests.post") as post:
            post.return_value = MagicMock()
            with self.assertRaises(bot_lp.FuenteError):
                scraping_la_plata()
            post.assert_called_once()
            texto = post.call_args.kwargs["json"]["text"]
            self.assertIn("falló el scrapeo", texto)
            self.assertIn("ConnectionError", texto)

    def test_redacta_token_en_errores(self):
        self.assertEqual(bot_lp._redactar("boom tok-test leaked", "tok-test"), "boom *** leaked")

    def test_mensaje_incluye_fecha_corta(self):
        mensajes = armar_mensajes([{"fecha": "2026-11-21 14:10", "evento": "Choque en 7 y 50", "url": "https://x/n1"}])
        self.assertIn("• [21/11 14:10] Choque en 7 y 50", mensajes[0])


class ZonaHorariaTests(unittest.TestCase):
    def test_usa_nombre_canonico(self):
        # "America/Buenos_Aires" es un alias legacy que Ubuntu 24.04+ no instala.
        self.assertEqual(bot_lp.TZ.key, "America/Argentina/Buenos_Aires")


class PalabrasAmpliadasTests(unittest.TestCase):
    def test_plurales(self):
        self.assertEqual(palabras_encontradas("Ola de robos en Tolosa"), ["robos"])
        self.assertEqual(palabras_encontradas("Dos incendios en Berisso"), ["incendios"])
        self.assertEqual(palabras_encontradas("Persecuciones y choques en la Ruta 2"), ["choques", "persecuciones"])

    def test_con_y_sin_tildes(self):
        self.assertEqual(palabras_encontradas("Le robó la moto"), ["robó"])
        self.assertEqual(palabras_encontradas("Caos de transito en el centro"), ["transito"])
        self.assertEqual(palabras_encontradas("Inundación en Los Hornos"), ["inundación"])

    def test_nuevas_palabras_de_servicio_y_accidentes(self):
        self.assertEqual(palabras_encontradas("Apagón masivo en el casco urbano"), ["apagón"])
        self.assertEqual(palabras_encontradas("Grave accidente en 7 y 50"), ["accidente"])
        self.assertEqual(palabras_encontradas("Chocaron dos autos en Gonnet"), ["chocaron"])
        self.assertEqual(palabras_encontradas("Balearon a un joven en Altos de San Lorenzo"), ["balearon"])

    def test_gas_requiere_contexto(self):
        self.assertEqual(palabras_encontradas("Fuga de gas obligó a evacuar una escuela"), ["gas"])
        self.assertEqual(palabras_encontradas("Suben las tarifas del gas"), [])

    def test_suprema_corte_no_es_corte(self):
        self.assertEqual(palabras_encontradas("La Suprema Corte bonaerense dejó firme una absolución"), [])
        self.assertEqual(palabras_encontradas("Un caso llegó a la Corte Suprema"), [])
        self.assertEqual(palabras_encontradas("Licitan el corte de pasto en plazas"), [])
        self.assertEqual(palabras_encontradas("La Corte bonaerense falló a favor"), [])

    def test_corte_de_servicio_sigue_matcheando(self):
        self.assertEqual(palabras_encontradas("Anuncian cortes de tránsito por obras"), ["cortes", "tránsito"])
        self.assertEqual(palabras_encontradas("Convocan al corte de la Ruta 2"), ["corte"])
        self.assertEqual(palabras_encontradas("ABSA anunció un corte prolongado de agua"), ["absa", "agua", "corte"])

    def test_no_matchea_dentro_de_otra_palabra(self):
        self.assertEqual(palabras_encontradas("Recortes en el presupuesto universitario"), [])
        self.assertEqual(palabras_encontradas("La política bonaerense en debate"), [])


SITEMAP_NEWS = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
  <url>
    <loc>https://www.0221.com.ar/policiales/fuerte-choque-en-romero-n200</loc>
    <news:news>
      <news:publication_date>2026-11-22T08:15:00-03:00</news:publication_date>
      <news:title>Fuerte choque en Melchor Romero dejó dos heridos</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.0221.com.ar/deportes/choque-de-estilos-en-el-bosque-n201</loc>
    <news:news>
      <news:publication_date>2026-11-22T09:00:00-03:00</news:publication_date>
      <news:title>Choque de estilos en el Bosque</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.0221.com.ar/la-plata/incendio-viejo-n150</loc>
    <news:news>
      <news:publication_date>2026-11-10T09:00:00-03:00</news:publication_date>
      <news:title>Un incendio viejo que no debería volver a avisarse</news:title>
    </news:news>
  </url>
  <url>
    <loc>https://www.0221.com.ar/la-plata/festival-n202</loc>
    <news:news>
      <news:publication_date>2026-11-22T10:00:00-03:00</news:publication_date>
      <news:title>Festival de la Gastronomía en La Plata</news:title>
    </news:news>
  </url>
</urlset>
"""

PORTADA = """
<html>
  <h2 class="news-article__title"><a href="/policiales/fuerte-choque-en-romero-n200">Video: el fuerte choque en Romero</a></h2>
  <h2 class="news-article__title"><a href="/la-plata/apagon-en-gonnet-n203">Apagón en Gonnet: vecinos sin luz desde anoche</a></h2>
</html>
"""

AHORA = datetime(2026, 11, 22, 12, 0, tzinfo=bot_lp.TZ)


def _resp(texto: str) -> MagicMock:
    resp = MagicMock()
    resp.text = texto
    resp.content = texto.encode("utf-8")
    resp.url = bot_lp.URL
    return resp


def _sesion(sitemap: object, portada: object) -> MagicMock:
    """Sesión falsa: cada URL devuelve un texto o lanza la excepción indicada."""

    def get(url, **_kwargs):
        valor = sitemap if url == bot_lp.URL_SITEMAP_NEWS else portada
        if isinstance(valor, BaseException):
            raise valor
        return _resp(valor)

    sesion = MagicMock()
    sesion.get.side_effect = get
    return sesion


class FuentesTests(unittest.TestCase):
    def test_clave_nota(self):
        self.assertEqual(bot_lp.clave_nota("https://www.0221.com.ar/la-plata/algo-n137814"), "137814")
        self.assertEqual(bot_lp.clave_nota("https://www.0221.com.ar/la-plata"), "")
        self.assertEqual(bot_lp.clave_nota(""), "")

    def test_sitemap_filtra_seccion_ventana_y_palabras(self):
        items = bot_lp.parse_sitemap_urlset(SITEMAP_NEWS)
        filas = bot_lp.filas_desde_sitemap(items, AHORA)
        self.assertEqual([f["url"] for f in filas], ["https://www.0221.com.ar/policiales/fuerte-choque-en-romero-n200"])
        self.assertEqual(filas[0]["fecha"], "2026-11-22 08:15")
        self.assertEqual(filas[0]["palabras"], "choque,heridos")

    def test_sitemap_sin_titulos_es_falla(self):
        vacio = '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'
        with self.assertRaises(bot_lp.FuenteError):
            bot_lp.leer_sitemap_news(_sesion(vacio, PORTADA), AHORA)

    def test_portada_sin_titulares_es_falla(self):
        with self.assertRaises(bot_lp.FuenteError):
            bot_lp.leer_portada(_sesion(SITEMAP_NEWS, "<html><p>mantenimiento</p></html>"), AHORA)

    def test_une_fuentes_sin_repetir_la_misma_nota(self):
        hallazgos, fallas = bot_lp.recolectar(_sesion(SITEMAP_NEWS, PORTADA), AHORA)
        self.assertEqual(fallas, [])
        nuevos = bot_lp.nuevos_eventos(hallazgos, set(), set())
        ids = [bot_lp.clave_nota(f["url"]) for f in nuevos]
        self.assertEqual(sorted(ids), ["200", "203"])
        choque = next(f for f in nuevos if f["url"].endswith("n200"))
        self.assertEqual(choque["evento"], "Fuerte choque en Melchor Romero dejó dos heridos")

    def test_descarta_nota_ya_guardada_con_otro_titulo(self):
        fila = {"fecha": "2026-11-22 12:00", "evento": "Título nuevo", "url": "https://www.0221.com.ar/x/y-n137814", "palabras": "choque"}
        self.assertEqual(bot_lp.nuevos_eventos([fila], set(), {"137814"}), [])

    def test_falla_parcial_sigue_y_avisa(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bot_lp, "ARCHIVO", Path(tmp) / "h.csv"), patch(
            "enriquecer.SALIDA", Path(tmp) / "n.geojson"
        ), patch(
            "bot_lp.crear_sesion", return_value=_sesion(bot_lp.requests.HTTPError("404"), PORTADA)
        ), patch.dict(os.environ, ENV_TG, clear=False), patch("bot_lp.requests.post") as post:
            post.return_value = MagicMock()
            nuevos = scraping_la_plata(AHORA)
            self.assertEqual(nuevos, 2)
            textos = [c.kwargs["json"]["text"] for c in post.call_args_list]
            self.assertTrue(any("2 evento(s) nuevo(s)" in t for t in textos))
            self.assertTrue(any("falló una fuente" in t and "sitemap" in t for t in textos))
            with (Path(tmp) / "h.csv").open(encoding="utf-8") as fh:
                self.assertEqual(len(list(csv.DictReader(fh))), 2)

    def test_segunda_corrida_no_repite(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(bot_lp, "ARCHIVO", Path(tmp) / "h.csv"), patch(
            "enriquecer.SALIDA", Path(tmp) / "n.geojson"
        ), patch(
            "bot_lp.crear_sesion", side_effect=lambda: _sesion(SITEMAP_NEWS, PORTADA)
        ), patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            self.assertEqual(scraping_la_plata(AHORA), 2)
            self.assertEqual(scraping_la_plata(AHORA), 0)


if __name__ == "__main__":
    unittest.main()
