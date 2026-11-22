import unittest
from datetime import datetime

from bot_lp import TZ, palabras_encontradas
from clasificacion import clasificar
from enriquecer import enriquecer_texto


class TipoYCategoriaTests(unittest.TestCase):
    def test_anuncio_corte_luz(self):
        r = clasificar(
            "Edelap anunció tareas en pleno centro y podrían producirse cortes de luz",
            "La empresa anunció tareas de mantenimiento en cuatro sectores.",
        )
        self.assertEqual(r["tipo_nota"], "anuncio")
        self.assertEqual(r["categoria"], "corte_luz")
        self.assertEqual(r["gravedad"], "amplio")

    def test_seguimiento_judicial(self):
        r = clasificar("Condenaron a la Municipalidad de La Plata por la inundación de 2013", "")
        self.assertEqual(r["tipo_nota"], "seguimiento_judicial")
        self.assertEqual(r["categoria"], "temporal_inundacion")

    def test_hecho_fatal(self):
        r = clasificar("Un motociclista chocó contra un auto estacionado y murió", "Su estado era crítico.")
        self.assertEqual(r["tipo_nota"], "hecho")
        self.assertEqual(r["categoria"], "accidente_vial")
        self.assertEqual(r["gravedad"], "fatal")

    def test_opinion(self):
        r = clasificar("Piden descentralizar la Policía para reforzar la seguridad en los barrios", "")
        self.assertEqual(r["tipo_nota"], "opinion_politica")

    def test_robo_entradera(self):
        r = clasificar("Una entradera a mano armada en Tolosa dejó dos heridos", "")
        self.assertEqual(r["categoria"], "robo")
        self.assertEqual(r["gravedad"], "arma_o_entradera")

    def test_fecha_anoche(self):
        pub = datetime(2026, 11, 22, 10, 39, tzinfo=TZ)
        r = clasificar("Robo en Tolosa", "Los ladrones entraron anoche a la casa.", pub)
        self.assertFalse(r["fecha_hecho_es_publicacion"])
        self.assertEqual(r["fecha_hecho"], "2026-11-21 22:00")

    def test_este_jueves_a_futuro_si_aun_no_paso(self):
        pub = datetime(2026, 11, 11, 19, 49, tzinfo=TZ)  # miércoles
        r = clasificar("Edelap realizará trabajos", "Las tareas serán este jueves en distintos puntos.", pub)
        self.assertFalse(r["fecha_hecho_es_publicacion"])
        self.assertEqual(r["fecha_hecho"], "2026-11-12 19:00")

    def test_durante_la_madrugada(self):
        pub = datetime(2026, 11, 10, 16, 16, tzinfo=TZ)
        r = clasificar("Robo en una casa", "El hecho ocurrió durante la madrugada en la vivienda.", pub)
        self.assertFalse(r["fecha_hecho_es_publicacion"])
        self.assertEqual(r["fecha_hecho"], "2026-11-10 03:00")

    def test_pedido_de_secuestro_no_es_gravedad_alta(self):
        r = clasificar(
            "Persecución en moto: adolescente detenido",
            "El vehículo tenía un pedido de secuestro activo por hurto desde diciembre de 2025.",
        )
        self.assertEqual(r["categoria"], "robo")
        self.assertEqual(r["gravedad"], "hurto")

    def test_fecha_este_sabado(self):
        pub = datetime(2026, 11, 22, 16, 0, tzinfo=TZ)  # domingo
        r = clasificar("Choque en 32 y 148", "El episodio ocurrió este sábado en la zona oeste.", pub)
        self.assertFalse(r["fecha_hecho_es_publicacion"])
        self.assertEqual(r["fecha_hecho"], "2026-11-21 16:00")


class PatronesNuevosTests(unittest.TestCase):
    def test_ya_no_dispara_solo_policia(self):
        self.assertEqual(palabras_encontradas("Piden más policía en los barrios de La Plata"), [])

    def test_nuevos_terminos(self):
        self.assertIn("entradera", palabras_encontradas("Una entradera en Gonnet"))
        self.assertTrue(palabras_encontradas("Lo asaltaron a mano armada"))
        self.assertTrue(palabras_encontradas("El motociclista murió en el acto"))
        self.assertTrue(palabras_encontradas("Hubo un despiste en la Ruta 2"))
        self.assertTrue(palabras_encontradas("Una colisión fatal en 13 y 44"))


class EnriquecerSinRedTests(unittest.TestCase):
    def test_compone_nota(self):
        nota = enriquecer_texto(
            url="https://www.0221.com.ar/la-plata/choque-en-7-y-50-n137000",
            titulo="Grave choque en 7 y 50 dejó dos heridos",
            fecha_pub="2026-11-20 15:00",
            cuerpo="El accidente ocurrió esta tarde en 7 y 50, en el casco urbano.",
        )
        self.assertEqual(nota["id"], "137000")
        self.assertEqual(nota["tipo_nota"], "hecho")
        self.assertEqual(nota["categoria"], "accidente_vial")
        self.assertEqual(nota["gravedad"], "heridos")
        self.assertEqual(nota["precision"], "esquina")
        self.assertEqual(nota["lugar_texto"], "7 y 50")
        self.assertIsNotNone(nota["lat"])
        self.assertFalse(nota["fecha_hecho_es_publicacion"])


if __name__ == "__main__":
    unittest.main()
