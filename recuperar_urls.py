#!/usr/bin/env python3
"""Completa la columna url de filas viejas del historial emparejando titulares
con los sitemaps mensuales de 0221 (news-full).

No envía Telegram, no reordena ni deduplica el CSV. Las filas sin un match
confiable quedan sin URL.

  python recuperar_urls.py
  python recuperar_urls.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import requests

from backfill import (
    SITEMAP_INDEX,
    ThrottledSession,
    extraer_articulo,
    meses_solapados,
    normalizar_historial,
    parse_fecha_arg,
    recolectar_sitemaps,
    texto_slug,
    urls_sitemap_mensual,
    escribir_historial,
)
from bot_lp import SECCIONES, TZ, clave_nota, parse_fecha_iso, ruta_historial, seccion_de

_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜñÑ", "aeiouuAEIOUUnN")
_STOP = frozenset(
    {
        "de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas",
        "y", "e", "o", "u", "en", "con", "por", "para", "a", "al", "que",
        "se", "su", "sus", "lo", "como", "mas", "ya", "fue", "video", "asi",
        "tras", "sobre", "desde", "hasta", "entre", "ante", "sin", "contra",
        "durante", "segun", "esta", "este", "esto", "hay",
    }
)
SECCIONES_DEFAULT = set(SECCIONES)
VENTANA_DIAS_DEFAULT = 3
# Umbrales calibrados con las 46 filas que ya tenían URL (todas recuperadas en automático).
UMBRAL_ALTA = 0.80
UMBRAL_ALTA_CON_MARGEN = 0.70
MARGEN_ALTA = 0.08
UMBRAL_DUDOSA = 0.55


def _plano(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").translate(_SIN_TILDES)).strip().lower()


def slug_titulo(titulo: str) -> str:
    """Normaliza un titular al estilo de los slugs de 0221 (sin tildes ni signos)."""
    return re.sub(r"[^a-z0-9]+", "-", _plano(titulo)).strip("-")


def slug_de_url(url: str) -> str:
    return re.sub(r"-n\d+$", "", texto_slug(url).replace(" ", "-")).strip("-")


def _tokens(slug: str) -> set[str]:
    return {t for t in slug.split("-") if t and t not in _STOP and not t.isdigit()}


def puntaje(slug_titular: str, slug_url: str) -> float:
    """Combinación de similitud de cadena y de tokens (sin stopwords)."""
    sa, sb = _tokens(slug_titular), _tokens(slug_url)
    jaccard = (len(sa & sb) / len(sa | sb)) if sa and sb else 0.0
    ratio = SequenceMatcher(None, slug_titular, slug_url).ratio()
    return 0.45 * ratio + 0.55 * jaccard


def parse_fecha_fila(valor: str) -> datetime | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return parse_fecha_iso(valor)


@dataclass
class Candidato:
    url: str
    puntaje: float
    lastmod: datetime | None
    slug: str


def indice_por_dia(items: list[dict], secciones: set[str] | None = None) -> dict:
    """Agrupa ítems del sitemap por fecha de lastmod/publicación (día local)."""
    por_dia: dict = defaultdict(list)
    permitidas = secciones or SECCIONES_DEFAULT
    for item in items:
        if permitidas and seccion_de(item["url"]) not in permitidas:
            continue
        dt = item.get("publicado") or item.get("lastmod")
        if dt is None:
            continue
        slug = slug_de_url(item["url"])
        por_dia[dt.astimezone(TZ).date()].append(
            {"url": item["url"], "slug": slug, "lastmod": dt, "titulo": item.get("titulo") or ""}
        )
    return por_dia


def candidatos_para(
    titulo: str,
    fecha: datetime,
    por_dia: dict,
    ventana: int = VENTANA_DIAS_DEFAULT,
    minimo: float = UMBRAL_DUDOSA,
) -> list[Candidato]:
    """Candidatos del sitemap en ±ventana días, ordenados por puntaje descendente."""
    st = slug_titulo(titulo)
    vistos: dict[str, Candidato] = {}
    dia0 = fecha.astimezone(TZ).date()
    for delta in range(-ventana, ventana + 1):
        for item in por_dia.get(dia0 + timedelta(days=delta), []):
            sc = puntaje(st, item["slug"])
            if sc < minimo:
                continue
            prev = vistos.get(item["url"])
            if prev is None or sc > prev.puntaje:
                vistos[item["url"]] = Candidato(item["url"], sc, item["lastmod"], item["slug"])
    return sorted(vistos.values(), key=lambda c: c.puntaje, reverse=True)


def confianza(cands: list[Candidato]) -> tuple[str, Candidato | None]:
    """Devuelve (alta|dudosa|baja, mejor candidato o None)."""
    if not cands:
        return "baja", None
    best = cands[0]
    segundo = cands[1].puntaje if len(cands) > 1 else 0.0
    if best.puntaje >= UMBRAL_ALTA or (best.puntaje >= UMBRAL_ALTA_CON_MARGEN and best.puntaje - segundo >= MARGEN_ALTA):
        if len(cands) > 1 and best.puntaje - segundo < 0.04 and best.puntaje < 0.92:
            return "dudosa", best
        return "alta", best
    if best.puntaje >= UMBRAL_DUDOSA:
        return "dudosa", best
    return "baja", best


def titulos_coinciden(titular: str, publicado: str) -> bool:
    """Acepta el titular actual de la nota aunque 0221 lo haya retocado un poco."""
    if not publicado:
        return False
    if _plano(titular) == _plano(publicado):
        return True
    a, b = slug_titulo(titular), slug_titulo(publicado)
    if puntaje(a, b) >= 0.68:
        return True
    sa, sb = _tokens(a), _tokens(b)
    return bool(sa and sb and len(sa & sb) / len(sa | sb) >= 0.55)


def confirmar_en_nota(http: ThrottledSession, titular: str, url: str) -> bool:
    try:
        resp = http.get(url)
    except requests.RequestException as exc:
        logging.warning("No pude confirmar %s: %s", url, exc)
        return False
    titulo, _pub = extraer_articulo(resp.text)
    ok = titulos_coinciden(titular, titulo)
    logging.info("  confirmación %s | csv: %s | nota: %s", "sí" if ok else "no", titular[:60], (titulo or "")[:60])
    return ok


def asignar_urls(
    filas: list[dict[str, str]],
    items: list[dict],
    http: ThrottledSession | None = None,
    ventana: int = VENTANA_DIAS_DEFAULT,
    secciones: set[str] | None = None,
    confirmar_dudosas: bool = True,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Rellena `url` en una copia de las filas. No reordena ni borra duplicados."""
    por_dia = indice_por_dia(items, secciones)
    stats = {"ya_tenia": 0, "alta": 0, "dudosa_ok": 0, "dudosa_no": 0, "baja": 0, "sin_fecha": 0}
    # Evita reconfirmar el mismo titular+url.
    confirmados: dict[tuple[str, str], bool] = {}
    salida: list[dict[str, str]] = []
    for fila in filas:
        nueva = dict(fila)
        if (nueva.get("url") or "").strip():
            stats["ya_tenia"] += 1
            salida.append(nueva)
            continue
        fecha = parse_fecha_fila(nueva.get("fecha") or "")
        if fecha is None:
            stats["sin_fecha"] += 1
            salida.append(nueva)
            continue
        nivel, cand = confianza(candidatos_para(nueva.get("evento") or "", fecha, por_dia, ventana=ventana))
        if nivel == "alta" and cand:
            nueva["url"] = cand.url
            stats["alta"] += 1
        elif nivel == "dudosa" and cand and confirmar_dudosas and http is not None:
            clave = (_plano(nueva.get("evento") or ""), cand.url)
            if clave not in confirmados:
                confirmados[clave] = confirmar_en_nota(http, nueva.get("evento") or "", cand.url)
            if confirmados[clave]:
                nueva["url"] = cand.url
                stats["dudosa_ok"] += 1
            else:
                stats["dudosa_no"] += 1
        elif nivel == "dudosa":
            stats["dudosa_no"] += 1
        else:
            stats["baja"] += 1
        salida.append(nueva)
    return salida, stats


def rango_del_historial(filas: list[dict[str, str]]) -> tuple[datetime, datetime]:
    fechas = [parse_fecha_fila(f.get("fecha") or "") for f in filas]
    fechas = [d for d in fechas if d is not None]
    if not fechas:
        raise ValueError("El historial no tiene fechas parseables")
    return min(fechas), max(fechas)


def cargar_sitemaps(
    http: ThrottledSession,
    desde: datetime,
    hasta: datetime,
    cache_dir: Path | None = None,
) -> list[dict]:
    """Baja el índice news-full y los meses necesarios, con caché opcional en disco."""
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        meses = meses_solapados(desde, hasta)
        index_path = cache_dir / "sitemap-index.xml"
        if index_path.is_file():
            index_xml = index_path.read_text(encoding="utf-8")
        else:
            logging.info("Descargando índice de sitemaps news-full")
            index_xml = http.get(SITEMAP_INDEX).text
            index_path.write_text(index_xml, encoding="utf-8")
        items: list[dict] = []
        from bot_lp import parse_sitemap_urlset

        for sm_url in urls_sitemap_mensual(index_xml, meses):
            nombre = sm_url.rstrip("/").rsplit("/", 1)[-1]
            dest = cache_dir / nombre
            if dest.is_file() and dest.stat().st_size > 1000:
                logging.info("Sitemap en caché: %s", dest.name)
                bruto = dest.read_bytes()
            else:
                logging.info("Descargando %s", sm_url)
                resp = http.get(sm_url)
                dest.write_bytes(resp.content)
                bruto = resp.content
            items.extend(parse_sitemap_urlset(bruto))
        # Dedup por URL, preferir el que tenga título/publicado si aparece dos veces.
        por_url: dict[str, dict] = {}
        for it in items:
            prev = por_url.get(it["url"])
            if prev is None or (it.get("titulo") and not prev.get("titulo")):
                por_url[it["url"]] = it
        return list(por_url.values())
    return recolectar_sitemaps(http, desde, hasta)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description="Recupera URLs de filas viejas del historial (sin Telegram).")
    p.add_argument("--csv", default=None, help="Ruta del historial (default: HISTORIAL_PATH o historial_lp.csv)")
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--ventana", type=int, default=VENTANA_DIAS_DEFAULT, help="Días de holgura alrededor de la fecha de la fila")
    p.add_argument("--secciones", default=",".join(sorted(SECCIONES_DEFAULT)))
    p.add_argument("--sitemaps-dir", default="/tmp/sitemaps_0221", help="Caché de sitemaps (no se commitea)")
    p.add_argument("--sin-confirmar", action="store_true", help="No descarga notas para resolver empates")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    path = Path(args.csv) if args.csv else ruta_historial()
    filas = normalizar_historial(path)
    if not filas:
        logging.error("No hay filas en %s", path)
        return 2
    desde, hasta = rango_del_historial(filas)
    # Un mes de margen por si lastmod cae justo afuera.
    desde_sm = desde - timedelta(days=15)
    hasta_sm = hasta + timedelta(days=15)
    logging.info("Historial %s filas | fechas %s → %s", len(filas), desde.date(), hasta.date())

    http = ThrottledSession(delay=args.delay)
    items = cargar_sitemaps(http, desde_sm, hasta_sm, cache_dir=Path(args.sitemaps_dir) if args.sitemaps_dir else None)
    logging.info("URLs en sitemaps: %s", len(items))

    secciones = {s.strip() for s in args.secciones.split(",") if s.strip()}
    nuevas, stats = asignar_urls(
        filas,
        items,
        http=None if args.sin_confirmar else http,
        ventana=args.ventana,
        secciones=secciones,
        confirmar_dudosas=not args.sin_confirmar,
    )
    recuperadas = sum(1 for a, b in zip(filas, nuevas) if not (a.get("url") or "").strip() and (b.get("url") or "").strip())
    sin = sum(1 for f in nuevas if not (f.get("url") or "").strip())
    logging.info(
        "Asignadas ahora: %s (alta=%s, dudosa confirmada=%s) | dudosa rechazada=%s | sin match=%s | ya tenían=%s | siguen sin URL=%s",
        recuperadas,
        stats["alta"],
        stats["dudosa_ok"],
        stats["dudosa_no"],
        stats["baja"] + stats["sin_fecha"],
        stats["ya_tenia"],
        sin,
    )
    if args.dry_run:
        logging.info("Dry-run: no se escribe %s", path)
        return 0
    escribir_historial(path, nuevas)
    logging.info("CSV actualizado: %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
