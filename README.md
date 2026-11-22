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
   tiroteos, motochorros, policía, etc. Coincide con y sin tildes y en plural.
   `luz`, `agua` y `gas` solas solo cuentan con contexto de servicio ("corte de
   luz", "sin agua", "fuga de gas"…); "Suprema Corte", "corte de pasto" y
   similares no cuentan como corte.
3. Descarta lo que ya está en el CSV, por titular o por ID de nota (el
   `-nNNNNNN` de la URL; así no se repite una nota a la que le cambiaron el título).
4. Agrega lo nuevo al CSV, lo manda por Telegram y el workflow commitea el CSV.

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

## Tests

```bash
python -m unittest discover -s tests -v
```

Corren también en Actions (`.github/workflows/tests.yml`) en cada PR y en cada
push que toque código.
