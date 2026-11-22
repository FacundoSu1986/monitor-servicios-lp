import os
import sys
import unittest
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
        with patch.dict(os.environ, ENV_TG, clear=False), patch(
            "bot_lp.requests.get", side_effect=bot_lp.requests.ConnectionError("down")
        ), patch("bot_lp.requests.post") as post:
            post.return_value = MagicMock()
            with self.assertRaises(bot_lp.requests.ConnectionError):
                scraping_la_plata()
            post.assert_called_once()
            texto = post.call_args.kwargs["json"]["text"]
            self.assertIn("falló el scrapeo", texto)
            self.assertIn("ConnectionError", texto)

    def test_redacta_token_en_errores(self):
        self.assertEqual(bot_lp._redactar("boom tok-test leaked", "tok-test"), "boom *** leaked")


if __name__ == "__main__":
    unittest.main()
