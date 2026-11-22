#!/usr/bin/env python3
"""Recupera titulares de 0221 en un rango de fechas y los agrega a historial_lp.csv.

No envía notificaciones de Telegram: solo completa el historial (útil tras una
caída del cron). Usa los sitemaps del diario (news + news-full mensual) y, si
hace falta, descarga la nota para obtener título y fecha de publicación reales.

Ejemplo:
  python backfill.py --desde "2026-11-04 22:16" --hasta "2026-11-22"
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

import bot_lp
from bot_lp import (
    ARCHIVO,
    CAMPOS,
    TIMEOUT,
    TZ,
    _normalizar,
    leer_eventos_previos,
    palabras_encontradas,
)

UA = (
    "monitor-servicios-lp-backfill/1.0 "
    "(historial local de eventos; contacto: repo GitHub FacundoSu1986/monitor-servicios-lp)"
)
SITEMAP_INDEX = "https://www.0221.com.ar/sitemap/news-full/sitemap-index.xml"
SITEMAP_NEWS = "https://www.0221.com.ar/sitemap-news.xml"
SECCIONES_DEFAULT = ("la-plata", "policiales", "berisso", "ensenada")
NS_SM = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
NS_NEWS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


class ThrottledSession:
    """Cliente HTTP educado: pausa entre pedidos y respeta 429."""

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"})

    def get(self, url: str) -> requests.Response:
        espera = self._last + self.delay - time.monotonic()
        if espera > 0:
            time.sleep(espera)
        for intento in range(4):
            resp = self.session.get(url, timeout=TIMEOUT)
            self._last = time.monotonic()
            if resp.status_code == 429:
                retry = float(resp.headers.get("Retry-After") or (self.delay * (intento + 2)))
                logging.warning("0221 respondió 429; espero %.1fs", retry)
                time.sleep(retry)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp


def parse_fecha_arg(texto: str, fin_del_dia: bool = False) -> datetime:
    texto = texto.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(texto, fmt)
            if fmt == "%Y-%m-%d" and fin_del_dia:
                dt = dt.replace(hour=23, minute=59, second=59)
            return dt.replace(tzinfo=TZ)
        except ValueError:
            continue
    dt = datetime.fromisoformat(texto)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def parse_fecha_iso(texto: str) -> datetime | None:
    if not texto:
        return None
    texto = texto.strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def seccion_de(url: str) -> str:
    path = urlparse(url).path.lstrip("/")
    return path.split("/", 1)[0] if path else ""


def texto_slug(url: str) -> str:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"-n\d+$", "", slug)
    return slug.replace("-", " ")


def en_ventana(dt: datetime | None, desde: datetime, hasta: datetime) -> bool:
    return dt is not None and desde <= dt <= hasta


def _xml(contenido: bytes | str) -> BeautifulSoup:
    if isinstance(contenido, bytes):
        if contenido[:2] == b"\x1f\x8b":
            contenido = gzip.decompress(contenido)
        contenido = contenido.decode("utf-8", "replace")
    return BeautifulSoup(contenido, "xml")


def parse_sitemap_urlset(contenido: bytes | str) -> list[dict]:
    """Parsea un urlset (mensual o news). Devuelve loc, lastmod, título y fecha de publicación si hay."""
    soup = _xml(contenido)
    filas: list[dict] = []
    for url in soup.find_all("url"):
        loc = url.find("loc")
        if loc is None or not loc.get_text(strip=True):
            continue
        lastmod = url.find("lastmod")
        pub = url.find("publication_date") or url.find("news:publication_date")
        title = url.find("title") or url.find("news:title")
        filas.append(
            {
                "url": loc.get_text(strip=True),
                "lastmod": parse_fecha_iso(lastmod.get_text(strip=True) if lastmod else ""),
                "publicado": parse_fecha_iso(pub.get_text(strip=True) if pub else ""),
                "titulo": (title.get_text(strip=True) if title else ""),
            }
        )
    return filas


def meses_solapados(desde: datetime, hasta: datetime) -> list[str]:
    meses: list[str] = []
    year, month = desde.year, desde.month
    while (year, month) <= (hasta.year, hasta.month):
        meses.append(f"{year:04d}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return meses


def urls_sitemap_mensual(index_xml: str, meses: Iterable[str]) -> list[str]:
    soup = _xml(index_xml)
    wanted = {f"sitemap-{m}.xml.gz" for m in meses}
    locs: list[str] = []
    for loc in soup.find_all("loc"):
        url = loc.get_text(strip=True)
        if any(nombre in url for nombre in wanted):
            locs.append(url)
    return locs


def extraer_articulo(html: str) -> tuple[str, datetime | None]:
    """Título y fecha de publicación (datePublished / meta) desde el HTML de la nota."""
    soup = BeautifulSoup(html, "html.parser")
    titulo = ""
    publicado: datetime | None = None

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        crudo = script.string or script.get_text() or ""
        try:
            data = json.loads(crudo)
        except json.JSONDecodeError:
            continue
        items: list = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("@graph", [data])
        for item in items:
            if not isinstance(item, dict):
                continue
            if not titulo:
                titulo = (item.get("headline") or item.get("name") or "").strip()
            if publicado is None:
                publicado = parse_fecha_iso(str(item.get("datePublished") or item.get("dateCreated") or ""))

    if not titulo:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            titulo = og["content"].strip()
    if not titulo:
        h1 = soup.find("h1")
        if h1:
            titulo = " ".join(h1.get_text(" ", strip=True).split())

    if publicado is None:
        meta = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find(
            "meta", attrs={"name": "article:published_time"}
        )
        if meta and meta.get("content"):
            publicado = parse_fecha_iso(meta["content"])
    return titulo, publicado


def prefiltra(item: dict, secciones: set[str]) -> bool:
    if seccion_de(item["url"]) not in secciones:
        return False
    texto = item.get("titulo") or texto_slug(item["url"])
    return bool(palabras_encontradas(texto))


def recolectar_sitemaps(http: ThrottledSession, desde: datetime, hasta: datetime) -> list[dict]:
    por_url: dict[str, dict] = {}

    logging.info("Descargando índice de sitemaps news-full")
    index = http.get(SITEMAP_INDEX)
    for sm_url in urls_sitemap_mensual(index.text, meses_solapados(desde, hasta)):
        logging.info("Descargando %s", sm_url)
        resp = http.get(sm_url)
        for item in parse_sitemap_urlset(resp.content):
            por_url[item["url"]] = item

    logging.info("Descargando sitemap-news (títulos y fecha de publicación recientes)")
    try:
        news = http.get(SITEMAP_NEWS)
        for item in parse_sitemap_urlset(news.content):
            prev = por_url.get(item["url"], {})
            prev.update({k: v for k, v in item.items() if v})
            por_url[item["url"]] = prev
    except requests.RequestException as exc:
        logging.warning("No pude leer sitemap-news.xml: %s", exc)

    return list(por_url.values())


def enriquecer(http: ThrottledSession, item: dict) -> dict:
    """Completa título y fecha real si el sitemap no los trae, bajando la nota."""
    if item.get("titulo") and item.get("publicado"):
        item["fecha_aproximada"] = False
        return item
    logging.info("Leyendo nota %s", item["url"])
    resp = http.get(item["url"])
    titulo, publicado = extraer_articulo(resp.text)
    if titulo:
        item["titulo"] = titulo
    if publicado:
        item["publicado"] = publicado
        item["fecha_aproximada"] = False
    else:
        item["fecha_aproximada"] = True
    return item


def fecha_evento(item: dict) -> tuple[datetime | None, bool]:
    if item.get("publicado"):
        return item["publicado"], False
    return item.get("lastmod"), True


def normalizar_historial(path: Path) -> list[dict[str, str]]:
    """Lee el CSV viejo (fecha,evento) o el nuevo (con url y palabras) y unifica columnas."""
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        lector = csv.DictReader(fh)
        filas: list[dict[str, str]] = []
        for cruda in lector:
            extra = cruda.get(None) or []
            filas.append(
                {
                    "fecha": (cruda.get("fecha") or "").strip(),
                    "evento": (cruda.get("evento") or "").strip(),
                    "url": (cruda.get("url") or (extra[0] if len(extra) > 0 else "")).strip(),
                    "palabras": (cruda.get("palabras") or (extra[1] if len(extra) > 1 else "")).strip(),
                }
            )
    return [f for f in filas if f["evento"]]


def escribir_historial(path: Path, filas: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAMPOS)
        writer.writeheader()
        writer.writerows(filas)


def candidatos_a_filas(
    items: list[dict],
    desde: datetime,
    hasta: datetime,
    vistos: set[str],
) -> tuple[list[dict[str, str]], int, int]:
    """Aplica filtro de palabras y ventana. Devuelve filas nuevas, omitidos por fecha y por dedup."""
    nuevas: list[dict[str, str]] = []
    omitidos_fecha = 0
    omitidos_dedup = 0
    ya = set(vistos)
    for item in items:
        titulo = (item.get("titulo") or "").strip()
        if not titulo:
            continue
        palabras = palabras_encontradas(titulo)
        if not palabras:
            continue
        dt, _aprox = fecha_evento(item)
        if not en_ventana(dt, desde, hasta):
            omitidos_fecha += 1
            continue
        clave = _normalizar(titulo)
        if clave in ya:
            omitidos_dedup += 1
            continue
        ya.add(clave)
        assert dt is not None
        nuevas.append(
            {
                "fecha": dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M"),
                "evento": titulo,
                "url": item["url"],
                "palabras": ",".join(palabras),
            }
        )
    nuevas.sort(key=lambda f: f["fecha"])
    return nuevas, omitidos_fecha, omitidos_dedup


def run(args: argparse.Namespace) -> int:
    desde = parse_fecha_arg(args.desde)
    hasta = parse_fecha_arg(args.hasta, fin_del_dia=True)
    if hasta < desde:
        logging.error("La fecha --hasta es anterior a --desde")
        return 2
    secciones = {s.strip() for s in args.secciones.split(",") if s.strip()}
    path = Path(args.csv)
    logging.info("Ventana %s → %s (%s)", desde.isoformat(), hasta.isoformat(), TZ.key)
    logging.info("Secciones: %s", ", ".join(sorted(secciones)))

    http = ThrottledSession(delay=args.delay)
    brutos = recolectar_sitemaps(http, desde, hasta)
    logging.info("URLs en sitemaps del período: %s", len(brutos))

    pre = [i for i in brutos if prefiltra(i, secciones)]
    # Si lastmod cae fuera de una ventana holgada, igual lo miramos cuando ya hay fecha de publicación.
    pre_en_rango = []
    for item in pre:
        dt_ref = item.get("publicado") or item.get("lastmod")
        # Holgura: lastmod puede ser posterior a la publicación, o al revés.
        if dt_ref is None or en_ventana(dt_ref, desde, hasta) or item.get("publicado"):
            pre_en_rango.append(item)
        elif item.get("lastmod") and desde <= item["lastmod"] <= hasta:
            pre_en_rango.append(item)
    logging.info("Candidatos tras prefiltro de palabras: %s", len(pre_en_rango))

    enriquecidos: list[dict] = []
    for item in pre_en_rango:
        try:
            enriquecidos.append(enriquecer(http, item))
        except requests.RequestException as exc:
            logging.warning("No pude leer %s: %s", item.get("url"), exc)
            if item.get("titulo"):
                item["fecha_aproximada"] = not bool(item.get("publicado"))
                enriquecidos.append(item)

    existentes = normalizar_historial(path)
    vistos = {_normalizar(f["evento"]) for f in existentes}
    nuevas, fuera, dup = candidatos_a_filas(enriquecidos, desde, hasta, vistos)
    aprox = sum(1 for i in enriquecidos if i.get("fecha_aproximada") and _normalizar(i.get("titulo") or "") in {_normalizar(n["evento"]) for n in nuevas})

    logging.info("Nuevos: %s | ya estaban: %s | fuera de ventana (fecha real): %s | fecha aproximada: %s", len(nuevas), dup, fuera, aprox)
    for fila in nuevas:
        logging.info(" + %s | %s", fila["fecha"], fila["evento"])

    if args.dry_run:
        logging.info("Dry-run: no se escribe %s", path)
        return 0

    if nuevas or (existentes and list(existentes[0].keys()) != CAMPOS):
        escribir_historial(path, existentes + nuevas)
        logging.info("Historial actualizado: %s filas totales (%s nuevas)", len(existentes) + len(nuevas), len(nuevas))
    else:
        logging.info("Nada que agregar.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill del historial de 0221 (sin Telegram)")
    p.add_argument("--desde", required=True, help='Inicio de la ventana, hora de Argentina. Ej: "2026-11-04 22:16"')
    p.add_argument("--hasta", required=True, help='Fin de la ventana. Si es solo una fecha, incluye ese día hasta las 23:59')
    p.add_argument("--csv", default=str(ARCHIVO), help="Ruta del historial (default: historial_lp.csv)")
    p.add_argument("--secciones", default=",".join(SECCIONES_DEFAULT), help="Secciones de 0221, separadas por coma")
    p.add_argument("--delay", type=float, default=1.0, help="Pausa en segundos entre pedidos HTTP (default: 1.0)")
    p.add_argument("--dry-run", action="store_true", help="No escribe el CSV")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    # Guardarraíl: este script nunca debe disparar avisos de Telegram.
    original_eventos = bot_lp.notificar_eventos
    original_error = bot_lp.notificar_error
    bot_lp.notificar_eventos = lambda *a, **k: None  # type: ignore[assignment]
    bot_lp.notificar_error = lambda *a, **k: None  # type: ignore[assignment]
    try:
        return run(parse_args(argv))
    finally:
        bot_lp.notificar_eventos = original_eventos
        bot_lp.notificar_error = original_error


if __name__ == "__main__":
    sys.exit(main())
