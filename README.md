# monitor-servicios-lp

Historial de servicios y eventos en Gran La Plata: cortes de luz, agua y gas,
cortes de tránsito, accidentes, incendios y hechos policiales publicados por el
diario [0221](https://www.0221.com.ar). Los guarda en `historial_lp.csv` y avisa
los nuevos por Telegram.

## Cómo funciona

`bot_lp.py` corre dos veces por día en GitHub Actions (`.github/workflows/main.yml`):

1. Lee dos fuentes de 0221:
   - **Sitemap de noticias** (`/sitemap-news.xml`): notas de las últimas ~48 h con
     título y fecha de publicación. Se toman las secciones `la-plata`,
     `policiales`, `berisso` y `ensenada` (`SECCIONES` en `bot_lp.py`). Como cubre
     dos días, no se pierden notas aunque GitHub atrase o saltee una corrida.
   - **Portada de La Plata** (`/la-plata`): lo que está destacado en el momento.
2. Se queda con los titulares que tienen palabras de alerta (`PATRONES_ALERTA`):
   cortes, apagón, Edelap, ABSA, Camuzzi, tránsito, inundación, alertas
   meteorológicas, accidentes, choques, vuelcos, incendios, robos, asaltos,
   tiroteos, motochorros, entraderas, colisiones, etc. Coincide con y sin tildes y en plural.
   `luz`, `agua` y `gas` solas solo cuentan con contexto de servicio ("corte de
   luz", "sin agua", "fuga de gas"…); "Suprema Corte", "corte de pasto" y
   similares no cuentan como corte.
3. Descarta lo que ya está en el CSV, por titular o por ID de nota (el
   `-nNNNNNN` de la URL; así no se repite una nota a la que le cambiaron el título).
4. Agrega lo nuevo al CSV, descarga el cuerpo de cada nota nueva, extrae lugar
   (esquina, cuadra, barrio o localidad) y la clasifica (tipo, categoría, gravedad
   y fecha del hecho). El resultado va a `notas_enriquecidas.geojson`. Solo las
   precisiones `esquina` y `cuadra` llevan coordenadas; un barrio o localidad no
   se dibuja como punto en el centroide (evita zonas calientes falsas).
5. Manda lo nuevo por Telegram y el workflow commitea el CSV y el GeoJSON.

Si una fuente falla, el bot sigue con la otra y avisa por Telegram. Si fallan
las dos (o una responde sin titulares, por ejemplo porque cambió el HTML), sale
con error y el workflow avisa el fallo.

### Columnas de `historial_lp.csv`

| columna    | contenido                                                                 |
|------------|---------------------------------------------------------------------------|
| `fecha`    | publicación de la nota (sitemap) o momento en que se detectó (portada), hora Argentina |
| `evento`   | titular                                                                   |
| `url`      | link a la nota (vacío en filas viejas)                                    |
| `palabras` | palabras de alerta encontradas (vacío en filas viejas)                    |

## Configuración

En *Settings → Secrets and variables → Actions* del repo:

- `TELEGRAM_BOT_TOKEN`: token del bot (lo da BotFather en Telegram).
- `TELEGRAM_CHAT_ID`: chat o grupo donde llegan los avisos.

Sin esos secretos el bot funciona igual, solo no avisa.

El horario está en el `cron` de `main.yml` (UTC). Un segundo cron semanal hace
un commit vacío si no hubo actividad en 50 días, para que GitHub no desactive
el workflow.

## Uso local

```bash
pip install -r requirements.txt
```

```bash
python bot_lp.py
```

Para correrlo sin mandar avisos, no definas `TELEGRAM_BOT_TOKEN` ni `TELEGRAM_CHAT_ID`.

### Recuperar un período perdido

`backfill.py` completa el historial de un rango de fechas a partir de los
sitemaps mensuales de 0221. No manda avisos por Telegram.

```bash
python backfill.py --desde "2026-11-04 22:16" --hasta "2026-11-22" --dry-run
```

Sin `--dry-run` escribe el CSV.

### Recuperar URLs de filas viejas

Las filas anteriores a que el bot guardara el link quedan con `url` vacío.
`recuperar_urls.py` las empareja con los sitemaps mensuales de 0221 (título → slug
de la URL, ±3 días alrededor de la fecha de la fila). Solo descarga la nota si el
match es dudoso, para confirmar el titular. No manda Telegram, no reordena el CSV
y deja sin URL lo que no sea confiable.

```bash
python recuperar_urls.py --dry-run
python recuperar_urls.py
python enriquecer.py --rehacer --cache-dir /tmp/enriquecer_cache
```



## Ubicación y clasificación

`ubicacion.py` y `clasificacion.py` trabajan sobre el titular más el cuerpo de la
nota. Las esquinas se geocodifican en local con `data/intersecciones.json`
(cruces precalculados de la red OSM de La Plata, Berisso y Ensenada). Los barrios
y localidades están en `data/barrios.json`; sus centroides sirven solo para
desambiguar cruces que existen en más de un lado de la ciudad, no como punto
del evento. No se usa un geocodificador externo.

Precisión posible: `esquina`, `cuadra`, `barrio`, `localidad`, `sin_lugar`.

Tipo de nota: `hecho`, `seguimiento_judicial`, `anuncio`, `opinion_politica`.
Categorías: cortes de luz/agua/gas, accidente vial, robo, homicidio o balacera,
incendio, temporal o inundación, corte de calle o piquete, u `otro`.

`historial_lp.csv` sigue siendo el registro crudo. El archivo derivado no se
usa para avisos: reprocesar el historial no manda nada por Telegram.

```bash
python enriquecer.py --rehacer
```

`scripts/build_intersecciones.py` regenera el JSON de cruces a partir de una
descarga Overpass (una sola vez; no corre en el bot).


## Mapa de eventos

`eventos.py` agrupa las notas de `notas_enriquecidas.geojson` en hechos
(misma categoría, lugar a ≤300 m o mismo barrio, y menos de 48 h). El resultado
es `eventos.geojson` (también se copia a `docs/`). Se regenera en cada corrida
del bot, sin volver a scrapear y sin Telegram.

El GeoJSON público (`docs/eventos.geojson` y la copia en la raíz del repo) y
`notas_enriquecidas.geojson` no incluyen titulares: detectar nombres propios
con reglas es frágil (calles, barrios, instituciones). El mapa muestra
categoría, lugar, fecha, gravedad y un enlace «Ver nota en 0221». Fuente:
[0221 (www.0221.com.ar)](https://www.0221.com.ar). Es un proyecto
independiente, no afiliado a 0221. El historial crudo (`historial_lp.csv`)
sigue teniendo los titulares originales.

La página estática está en `docs/index.html` (Leaflet + Leaflet.heat). Lee
`./eventos.geojson`, así se actualiza sola cuando el workflow commitea el archivo.
Una capa por categoría; el calor (pesado por gravedad) es solo para robos y
accidentes, nunca un único mapa mezclado. Los puntos son esquinas o cuadras;
los barrios van como mancha suave y en un listado; los `sin_lugar` no se dibujan.

```bash
python enriquecer.py --rehacer --cache-dir /tmp/enriquecer_cache
python eventos.py
python -m http.server 8000 --directory docs
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Corren también en Actions (`.github/workflows/tests.yml`) en cada PR y en cada
push que toque código.
