#!/usr/bin/env python3
"""Agrupa notas enriquecidas en eventos (una marca por hecho) y escribe eventos.geojson.

Reprocesable desde notas_enriquecidas.geojson, sin volver a scrapear ni Telegram.

  python eventos.py
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from bot_lp import FORMATO_FECHA, TZ, parse_fecha_iso
from enriquecer import SALIDA as NOTAS_GEOJSON

_REPO = Path(__file__).resolve().parent
SALIDA = Path("eventos.geojson")
SALIDA_DOCS = _REPO / "docs" / "eventos.geojson"
_CANON_EVENTOS = _REPO / "eventos.geojson"
DATA_BARRIOS = Path(__file__).resolve().parent / "data" / "barrios.json"

PREC_ORDEN = {"esquina": 5, "cuadra": 4, "barrio": 3, "localidad": 2, "sin_lugar": 1}
GRAV_ORDEN = {
    "fatal": 50,
    "alta": 40,
    "arma_o_entradera": 40,
    "heridos": 30,
    "media": 30,
    "amplio": 30,
    "motochorro": 20,
    "danos": 10,
    "hurto": 10,
    "puntual": 10,
    "baja": 5,
}
RADIO_M = 300
VENTANA = timedelta(hours=48)
_SIN = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")


def _plano(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").translate(_SIN)).strip().lower()


def parse_dt(valor: str | None) -> datetime | None:
    valor = (valor or "").strip()
    if not valor:
        return None
    dt = parse_fecha_iso(valor)
    if dt:
        return dt
    for fmt in (FORMATO_FECHA, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor, fmt).replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def fecha_para_cluster(nota: dict) -> datetime | None:
    """Usa fecha_hecho si no es solo la de publicación; si no, fecha_pub."""
    if not nota.get("fecha_hecho_es_publicacion"):
        dt = parse_dt(nota.get("fecha_hecho"))
        if dt:
            return dt
    return parse_dt(nota.get("fecha_pub") or nota.get("fecha_hecho"))


def distancia_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def coords(nota: dict) -> tuple[float, float] | None:
    lat, lon = nota.get("lat"), nota.get("lon")
    if lat is None or lon is None:
        return None
    if nota.get("precision") not in {"esquina", "cuadra"}:
        return None
    return float(lat), float(lon)


def mismo_lugar(a: dict, b: dict, radio_m: float = RADIO_M) -> bool:
    """Misma categoría se evalúa afuera. No agrupa dos sin_lugar."""
    if a.get("precision") == "sin_lugar" or b.get("precision") == "sin_lugar":
        return False
    ca, cb = coords(a), coords(b)
    if ca and cb:
        return distancia_m(ca, cb) <= radio_m
    la, lb = _plano(a.get("lugar_texto") or ""), _plano(b.get("lugar_texto") or "")
    if not la or not lb:
        return False
    if la == lb:
        return True
    # "gonnet" vs "25 y 514" no se fusionan si no hay coordenadas en ambos.
    return False


def _barrios_centroides() -> dict[str, tuple[float, float]]:
    if not DATA_BARRIOS.is_file():
        return {}
    datos = json.loads(DATA_BARRIOS.read_text(encoding="utf-8"))
    out: dict[str, tuple[float, float]] = {}
    for info in datos.values():
        if info.get("lat") is None or info.get("lon") is None:
            continue
        for alias in [info["nombre"], *(info.get("aliases") or [])]:
            out[_plano(alias)] = (float(info["lat"]), float(info["lon"]))
    return out


def notas_desde_geojson(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [feat.get("properties") or {} for feat in data.get("features") or []]


def agrupar_eventos(notas: list[dict], radio_m: float = RADIO_M, ventana: timedelta = VENTANA) -> list[list[dict]]:
    """Agrupación voraz: misma categoría, lugar compatible y menos de 48 h."""
    items: list[tuple[datetime, dict]] = []
    for n in notas:
        if not n.get("id"):
            continue
        dt = fecha_para_cluster(n)
        if dt is None:
            dt = datetime(1970, 1, 1, tzinfo=TZ)
        items.append((dt, n))
    items.sort(key=lambda x: (x[0], str(x[1].get("id"))))

    grupos: list[list[tuple[datetime, dict]]] = []
    for dt, nota in items:
        asignado = False
        for grupo in grupos:
            ref_dt, ref = grupo[0]
            # comparar con todos los del grupo (no solo el primero) para el radio y la fecha
            if any(
                gnota.get("categoria") == nota.get("categoria")
                and abs((gdt - dt).total_seconds()) <= ventana.total_seconds()
                and mismo_lugar(gnota, nota, radio_m=radio_m)
                for gdt, gnota in grupo
            ):
                grupo.append((dt, nota))
                asignado = True
                break
        if not asignado:
            grupos.append([(dt, nota)])
    return [[n for _, n in g] for g in grupos]


def _mejor_precision(notas: list[dict]) -> str:
    return max((n.get("precision") or "sin_lugar" for n in notas), key=lambda p: PREC_ORDEN.get(p, 0))


def _max_gravedad(notas: list[dict]) -> str:
    return max((n.get("gravedad") or "baja" for n in notas), key=lambda g: GRAV_ORDEN.get(g, 0))


def _fecha_hecho_evento(notas: list[dict]) -> str:
    confiables = [parse_dt(n.get("fecha_hecho")) for n in notas if not n.get("fecha_hecho_es_publicacion")]
    confiables = [d for d in confiables if d]
    if confiables:
        return min(confiables).strftime(FORMATO_FECHA)
    pubs = [parse_dt(n.get("fecha_pub") or n.get("fecha_hecho")) for n in notas]
    pubs = [d for d in pubs if d]
    return min(pubs).strftime(FORMATO_FECHA) if pubs else ""


def _lugar_representativo(notas: list[dict]) -> tuple[str, str, float | None, float | None]:
    prec = _mejor_precision(notas)
    candidatas = [n for n in notas if (n.get("precision") or "sin_lugar") == prec] or notas
    # preferir la que tiene coords
    candidatas = sorted(candidatas, key=lambda n: (0 if coords(n) else 1, n.get("id") or ""))
    n0 = candidatas[0]
    lat = lon = None
    if prec in {"esquina", "cuadra"}:
        puntos = [coords(n) for n in notas if coords(n)]
        if puntos:
            lat = round(sum(p[0] for p in puntos) / len(puntos), 6)
            lon = round(sum(p[1] for p in puntos) / len(puntos), 6)
    return n0.get("lugar_texto") or "", prec, lat, lon


def _confianza(n_notas: int, precision: str) -> str:
    if n_notas >= 2 and precision in {"esquina", "cuadra"}:
        return "alta"
    if n_notas >= 2 or precision in {"esquina", "cuadra"}:
        return "media"
    return "baja"


def evento_desde_grupo(notas: list[dict], centroides: dict[str, tuple[float, float]]) -> dict:
    notas_ord = sorted(notas, key=lambda n: (n.get("fecha_pub") or "", n.get("id") or ""))
    ancla = min(notas_ord, key=lambda n: (fecha_para_cluster(n) or datetime.max.replace(tzinfo=TZ), n.get("id") or ""))
    lugar, prec, lat, lon = _lugar_representativo(notas_ord)
    if lat is None and prec in {"barrio", "localidad"}:
        c = centroides.get(_plano(lugar))
        if c:
            # solo para el mapa de mancha / listado, no como geometría puntual
            lat, lon = c
            # but we should not put these in geometry; store as approx in properties
            # use lat_aprox? We'll set lat/lon in props for circles, geometry null.
            # To distinguish, keep lat/lon in props only for esquina/cuadra in geometry;
            # for barrio store lat_aprox. Simpler: always store lat/lon in props when known,
            # geometry only if prec esquina/cuadra.
            aprox = True
        else:
            aprox = False
    else:
        aprox = prec in {"barrio", "localidad"} and lat is not None

    props = {
        "id_evento": f"e{ancla.get('id')}",
        "categoria": ancla.get("categoria") or "otro",
        "gravedad": _max_gravedad(notas_ord),
        "fecha_hecho": _fecha_hecho_evento(notas_ord),
        "lugar_texto": lugar,
        "precision": prec,
        "lat": lat if not aprox and prec in {"esquina", "cuadra"} else (None if aprox else lat),
        "lon": lon if not aprox and prec in {"esquina", "cuadra"} else (None if aprox else lon),
        "lat_aprox": lat if aprox else None,
        "lon_aprox": lon if aprox else None,
        "urls": [n.get("url") or "" for n in notas_ord],
        "titulos": [n.get("titulo") or "" for n in notas_ord],
        "ids_notas": [str(n.get("id") or "") for n in notas_ord],
        "tipos_nota": sorted({n.get("tipo_nota") or "hecho" for n in notas_ord}),
        "n_notas": len(notas_ord),
        "confianza": _confianza(len(notas_ord), prec),
    }
    if prec in {"esquina", "cuadra"} and lat is not None and lon is not None:
        props["lat"] = lat
        props["lon"] = lon
        props["lat_aprox"] = None
        props["lon_aprox"] = None
    return props


def evento_a_feature(ev: dict) -> dict:
    lat, lon = ev.get("lat"), ev.get("lon")
    geom = None
    if ev.get("precision") in {"esquina", "cuadra"} and lat is not None and lon is not None:
        geom = {"type": "Point", "coordinates": [lon, lat]}
    return {"type": "Feature", "geometry": geom, "properties": ev}


def escribir_eventos(path: Path, eventos: list[dict]) -> None:
    eventos_ord = sorted(eventos, key=lambda e: (e.get("fecha_hecho") or "", e.get("id_evento") or ""))
    data = {
        "type": "FeatureCollection",
        "name": "eventos",
        "features": [evento_a_feature(e) for e in eventos_ord],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def construir_eventos(notas: list[dict]) -> list[dict]:
    centroides = _barrios_centroides()
    return [evento_desde_grupo(g, centroides) for g in agrupar_eventos(notas)]


def actualizar_eventos(
    notas: list[dict] | None = None,
    origen: Path | None = None,
    salida: Path | None = None,
    copiar_docs: bool | None = None,
) -> list[dict]:
    """Punto de enganche: reconstruye eventos.geojson (y la copia en docs/) sin scrapear."""
    origen = origen if origen is not None else NOTAS_GEOJSON
    salida = salida if salida is not None else SALIDA
    if notas is None:
        notas = notas_desde_geojson(origen)
    eventos = construir_eventos(notas)
    escribir_eventos(salida, eventos)
    if copiar_docs is None:
        copiar_docs = Path(salida).resolve() == _CANON_EVENTOS
    if copiar_docs:
        escribir_eventos(SALIDA_DOCS, eventos)
    logging.info("Eventos: %s grupos a partir de %s notas → %s", len(eventos), len(notas), salida)
    return eventos


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser(description="Arma eventos.geojson a partir de las notas enriquecidas (sin Telegram).")
    p.add_argument("--notas", default=str(NOTAS_GEOJSON))
    p.add_argument("--salida", default=str(SALIDA))
    args = p.parse_args(argv)
    ev = actualizar_eventos(origen=Path(args.notas), salida=Path(args.salida))
    logging.info("Listo: %s eventos", len(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
