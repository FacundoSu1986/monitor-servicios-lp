import unittest

from ubicacion import extraer_lugar, buscar_barrios


class EsquinaTests(unittest.TestCase):
    def test_esquina_alta_en_osm(self):
        r = extraer_lugar("El siniestro ocurrió este martes en la esquina de 25 y 514, en Gonnet.")
        self.assertEqual(r["precision"], "esquina")
        self.assertEqual(r["lugar_texto"], "25 y 514")
        self.assertIsNotNone(r["lat"])

    def test_avenida_alta_como_segundo_termino(self):
        r = extraer_lugar("El hecho ocurrió en la intersección de 155 y avenida 520, en Melchor Romero.")
        self.assertEqual(r["precision"], "esquina")
        self.assertEqual(r["lugar_texto"], "155 y 520")
        self.assertAlmostEqual(r["lat"], -34.9358, places=3)

    def test_domicilio_no_desvia_el_casco(self):
        r = extraer_lugar("La pelea fue en 49 entre 6 y 7. El conductor, de 49 años, domiciliado en Gonnet, huyó del lugar.")
        self.assertEqual(r["lugar_texto"], "49 entre 6 y 7")
        self.assertAlmostEqual(r["lat"], -34.9138, places=3)
        self.assertAlmostEqual(r["lon"], -57.9499, places=3)

    def test_siete_y_cincuenta_casco(self):
        r = extraer_lugar("El choque fue en 7 y 50, en el centro de La Plata.")
        self.assertEqual(r["precision"], "esquina")
        self.assertEqual(r["lugar_texto"], "7 y 50")
        self.assertAlmostEqual(r["lat"], -34.9148, places=3)
        self.assertAlmostEqual(r["lon"], -57.9497, places=3)

    def test_no_confunde_edad_con_esquina(self):
        r = extraer_lugar("Un hombre de 7 y 50 años fue internado en Gonnet.")
        self.assertNotEqual(r["lugar_texto"], "7 y 50")
        self.assertEqual(r["precision"], "localidad")
        self.assertEqual(r["lugar_texto"], "Gonnet")
        self.assertIsNone(r["lat"])

    def test_no_confunde_horario(self):
        r = extraer_lugar("Los trabajos serán entre las 8 y las 12 en Los Hornos.")
        self.assertEqual(r["lugar_texto"], "Los Hornos")
        self.assertEqual(r["precision"], "localidad")

    def test_no_confunde_conteos(self):
        r = extraer_lugar("En julio se registraron 14 y 43 víctimas, respectivamente, en La Plata.")
        self.assertNotIn(r["lugar_texto"], {"14 y 43", "14 y 4"})
        self.assertEqual(r["precision"], "sin_lugar")

    def test_calles_en_rango_no_es_esquina(self):
        r = extraer_lugar("En Tolosa trabajarán en el sector comprendido entre las calles 25 y 29 y de 529 a 532.")
        self.assertNotEqual(r["lugar_texto"], "25 y 29")

    def test_no_usa_domicilio_como_esquina(self):
        r = extraer_lugar("Ambas vivían en la zona de 612 y 124 y caminaban hasta la Ruta Provincial N°11, a la altura de la calle 609.")
        self.assertNotEqual(r["lugar_texto"], "612 y 124")


class CuadraTests(unittest.TestCase):
    def test_entre_calles_con_bis(self):
        r = extraer_lugar("El episodio ocurrió en avenida 32 entre 148 y 148 bis, zona oeste.")
        self.assertEqual(r["precision"], "cuadra")
        self.assertEqual(r["lugar_texto"], "32 entre 148 y 148 bis")
        self.assertIsNotNone(r["lat"])
        self.assertIsNotNone(r["lon"])

    def test_coma_antes_de_entre(self):
        r = extraer_lugar("El accidente ocurrió este sábado en avenida 32, entre 148 y 148 bis, y el joven fue trasladado al Hospital Alejandro Korn de Melchor Romero.")
        self.assertEqual(r["precision"], "cuadra")
        self.assertEqual(r["lugar_texto"], "32 entre 148 y 148 bis")
        self.assertIsNotNone(r["lat"])

    def test_cuadra_edelap(self):
        r = extraer_lugar("Las tareas están previstas en la zona de calle 42 entre 8 y 9, del lado par.")
        self.assertEqual(r["lugar_texto"], "42 entre 8 y 9")
        self.assertEqual(r["precision"], "cuadra")
        self.assertIsNotNone(r["lat"])

    def test_calle_alta_barrio_aeropuerto(self):
        r = extraer_lugar("El asalto ocurrió en 606 entre 115 y 116, en Barrio Aeropuerto.")
        self.assertEqual(r["precision"], "cuadra")
        self.assertEqual(r["lugar_texto"], "606 entre 115 y 116")
        self.assertIsNotNone(r["lat"])

    def test_altura(self):
        r = extraer_lugar("Lo detuvieron en calle 12 al 1500.")
        self.assertEqual(r["precision"], "cuadra")
        self.assertEqual(r["lugar_texto"], "12 al 1500")
        self.assertIsNone(r["lat"])

    def test_ruta_km(self):
        r = extraer_lugar("El vuelco fue en la Ruta 2 km 45.")
        self.assertEqual(r["lugar_texto"], "Ruta 2 km 45")
        self.assertEqual(r["precision"], "cuadra")

    def test_camino_centenario(self):
        r = extraer_lugar("Hay desvíos en Camino Centenario por un choque.")
        self.assertEqual(r["precision"], "cuadra")
        self.assertIn("Centenario", r["lugar_texto"])


class BarrioTests(unittest.TestCase):
    def test_localidad(self):
        r = extraer_lugar("Un incendio destruyó una vivienda precaria en Abasto.")
        self.assertEqual(r["precision"], "localidad")
        self.assertEqual(r["lugar_texto"], "Abasto")
        self.assertIsNone(r["lat"])

    def test_romero_no_come_melchor(self):
        nombres = [b["nombre"] for b in buscar_barrios("el choque fue en Melchor Romero, no en otro barrio")]
        self.assertEqual(nombres, ["Melchor Romero"])

    def test_hospital_no_cuenta_como_lugar_del_hecho(self):
        nombres = [b["nombre"] for b in buscar_barrios("ingresó al Hospital Alejandro Korn de Melchor Romero")]
        self.assertEqual(nombres, [])

    def test_la_plata_solo_es_sin_lugar(self):
        r = extraer_lugar("Así fue el incidente en La Plata el sábado.")
        self.assertEqual(r["precision"], "sin_lugar")
        self.assertEqual(r["lugar_texto"], "")

    def test_la_loma(self):
        r = extraer_lugar("La víctima residía en La Loma y la Justicia condenó a la Municipalidad.")
        self.assertEqual(r["lugar_texto"], "La Loma")
        self.assertEqual(r["precision"], "barrio")
        self.assertIsNone(r["lat"])

    def test_ruta_km_en_el_copete_gana_a_barrio_posterior(self):
        texto = (
            "Tragedia en la Ruta 6: una camioneta chocó con un camión.\n"
            "El grave incidente tuvo lugar alrededor de las 18.25 a la altura del kilómetro 42, "
            "en las inmediaciones del cruce con la Ruta 53, en un tramo que conecta San Vicente y La Plata.\n\n"
            "Las víctimas del tránsito en La Plata, Berisso y Ensenada siguen en aumento. "
            "Julio fue el mes con más fallecimientos registrados en Berisso."
        )
        r = extraer_lugar(texto)
        self.assertEqual(r["lugar_texto"], "Ruta 6 km 42")
        self.assertEqual(r["precision"], "cuadra")


if __name__ == "__main__":
    unittest.main()
