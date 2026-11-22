#!/usr/bin/env python3
"""Enriquece notas de 0221 (cuerpo, lugar, clasificación) y escribe un GeoJSON derivado.

No manda avisos por Telegram. `historial_lp.csv` queda como registro crudo.

  python enriquecer.py
  python enriquecer.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from backfill import extraer_articulo
from bot_lp import (
    ARCHIVO,
    FORMATO_FECHA,
    TIMEOUT,
    TZ,
    USER_AGENT,
    clave_nota,
    crear_sesion,
    parse_fecha_iso,
)
from clasificacion import clasificar
from ubicacion import extraer_lugar

SALIDA = Path("notas_enriquecidas.geojson")


def cuerpo_nota(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    partes: list[str] = []
    nodos = soup.select(".col-lg-9 .article-body") or soup.select(".article-body")
    vistos: set[str] = set()
    for n in nodos:
        t = " ".join(n.get_text(" ", strip=True).split())
        if not t or t in vistos:
            continue
        vistos.add(t)
        partes.append(t)
        if sum(len(p) for p in partes) > 6000:
            break
    return "\n".join(partes)


def parse_fecha_pub(valor: str) -> datetime | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    dt = parse_fecha_iso(valor)
    if dt:
        return dt
    try:
        return datetime.strptime(valor, FORMATO_FECHA).replace(tzinfo=TZ)
    except ValueError:
        return None


def id_nota(url: str, fallback: str = "") -> str:
    clave = clave_nota(url)
    if clave:
        return clave
    return fallback


def nota_a_feature(nota: dict) -> dict:
    lat, lon = nota.get("lat"), nota.get("lon")
    geom = {"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None
    props = {k: nota[k] for k in (
        "id", "url", "titulo", "fecha_pub", "fecha_hecho", "fecha_hecho_es_publicacion",
        "tipo_nota", "categoria", "gravedad", "lugar_texto", "precision",
    )}
    props["lat"] = lat
    props["lon"] = lon
    return {"type": "Feature", "geometry": geom, "properties": props}


def enriquecer_texto(
    *,
    url: str,
    titulo: str,
    fecha_pub: str,
    cuerpo: str,
) -> dict:
    pub = parse_fecha_pub(fecha_pub)
    lugar = extraer_lugar(f"{titulo}\n{cuerpo}")
    clase = clasificar(titulo, cuerpo, pub)
    return {
        "id": id_nota(url, fallback=titulo[:40]),
        "url": url,
        "titulo": titulo,
        "fecha_pub": fecha_pub,
        **clase,
        **lugar,
    }


def bajar_nota(sesion: requests.Session, url: str, delay: float, ultimo: list[float]) -> tuple[str, str, str]:
    espera = delay - (time.monotonic() - ultimo[0]) if ultimo[0] else 0
    if espera > 0:
        time.sleep(espera)
    resp = sesion.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, timeout=TIMEOUT)
    ultimo[0] = time.monotonic()
    resp.raise_for_status()
    titulo, publicado = extraer_articulo(resp.text)
    cuerpo = cuerpo_nota(resp.text)
    fecha = publicado.astimezone(TZ).strftime(FORMATO_FECHA) if publicado else ""
    return titulo, fecha, cuerpo


def leer_geojson(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        if props.get("id"):
            out[str(props["id"])] = props
    return out


def escribir_geojson(path: Path, notas: list[dict]) -> None:
    notas_ord = sorted(notas, key=lambda n: (n.get("fecha_pub") or "", n.get("id") or ""))
    data = {
        "type": "FeatureCollection",
        "name": "notas_enriquecidas",
        "features": [nota_a_feature(n) for n in notas_ord],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def enriquecer_filas(
    filas: list[dict[str, str]],
    sesion: requests.Session | None = None,
    existentes: dict[str, dict] | None = None,
    delay: float = 1.0,
    rehacer: bool = False,
) -> list[dict]:
    """Enriquece filas con URL. No envía Telegram. Reusa features ya calculados salvo --rehacer."""
    sesion = sesion or crear_sesion()
    existentes = dict(existentes or {})
    ultimo = [0.0]
    resultado: list[dict] = []
    vistas: set[str] = set()
    for fila in filas:
        url = (fila.get("url") or "").strip()
        if not url:
            continue
        nid = id_nota(url)
        if nid and nid in vistas:
            continue
        if nid and nid in existentes and not rehacer:
            resultado.append(existentes[nid])
            vistas.add(nid)
            continue
        titulo_csv = (fila.get("evento") or "").strip()
        fecha_csv = (fila.get("fecha") or "").strip()
        try:
            titulo, fecha_html, cuerpo = bajar_nota(sesion, url, delay, ultimo)
        except requests.RequestException as exc:
            logging.warning("No pude leer %s: %s", url, exc)
            cuerpo, titulo, fecha_html = "", titulo_csv, fecha_csv
        titulo = titulo or titulo_csv
        fecha_pub = fecha_html or fecha_csv
        nota = enriquecer_texto(url=url, titulo=titulo, fecha_pub=fecha_pub, cuerpo=cuerpo)
        resultado.append(nota)
        if nid:
            existentes[nid] = nota
            vistas.add(nid)
        logging.info(
            " %s | %s | %s | %s | %s",
            nota["precision"],
            nota["tipo_nota"],
            nota["categoria"],
            nota["lugar_texto"] or "—",
            titulo[:70],
        )
    return resultado


def enriquecer_nuevos(sesion: requests.Session, filas: list[dict[str, str]], salida: Path | None = None) -> None:
    """Punto de enganche del bot: suma las notas nuevas al GeoJSON sin tocar Telegram."""
    salida = salida if salida is not None else SALIDA
    if not filas:
        return
    prev = leer_geojson(salida)
    nuevas = enriquecer_filas(filas, sesion=sesion, existentes=prev, delay=1.0)
    por_id = prev
    for n in nuevas:
        por_id[str(n["id"])] = n
    escribir_geojson(salida, list(por_id.values()))
    logging.info("GeoJSON actualizado: %s notas en %s", len(por_id), salida)


def filas_con_url(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("url") or "").strip()]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description="Reprocesa el historial (solo filas con URL) y escribe el GeoJSON.")
    p.add_argument("--historial", default=str(ARCHIVO))
    p.add_argument("--salida", default=str(SALIDA))
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--rehacer", action="store_true", help="Ignora el GeoJSON previo y vuelve a bajar las notas")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    filas = filas_con_url(Path(args.historial))
    logging.info("Filas con URL: %s", len(filas))
    notas = enriquecer_filas(
        filas,
        sesion=crear_sesion(),
        existentes={} if args.rehacer else leer_geojson(Path(args.salida)),
        delay=args.delay,
        rehacer=args.rehacer,
    )
    if args.dry_run:
        logging.info("Dry-run: no se escribe %s (%s notas)", args.salida, len(notas))
        return 0
    escribir_geojson(Path(args.salida), notas)
    logging.info("Escrito %s con %s notas", args.salida, len(notas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
