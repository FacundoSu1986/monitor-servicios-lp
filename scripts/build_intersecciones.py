#!/usr/bin/env python3
"""Genera data/intersecciones.json a partir de un export Overpass (ways con geometría).

Uso:
  python scripts/build_intersecciones.py /tmp/osm_streets.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CALLE_RE = re.compile(
    r"^(?:Calle|Avenida|Av\.?)\s+(\d+)\s*(bis)?(?:\s*[A-Za-zÁÉÍÓÚÑ].*)?$",
    re.IGNORECASE,
)
DIAG_RE = re.compile(r"^Diagonal\s+(\d+)\s*(bis)?(?:\s|$)", re.IGNORECASE)
RUTA_RE = re.compile(
    r"^(?:Ruta(?:\s+Provincial|\s+Nacional)?|RP|RN)\s*N?º?\s*(\d+)\b",
    re.IGNORECASE,
)

NOMBRADAS = {
    "camino parque centenario": "camino_centenario",
    "avenida centenario": "camino_centenario",
    "camino general belgrano": "camino_belgrano",
    "camino centenario": "camino_centenario",
}


def _sin_tildes(texto: str) -> str:
    return texto.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")


def clave_calle(nombre: str) -> str | None:
    nombre = nombre.strip()
    m = CALLE_RE.match(nombre)
    if m:
        num = str(int(m.group(1)))
        return f"{num}bis" if m.group(2) else num
    m = DIAG_RE.match(nombre)
    if m:
        num = str(int(m.group(1)))
        base = f"d{num}bis" if m.group(2) else f"d{num}"
        return base
    m = RUTA_RE.match(nombre)
    if m:
        return f"ruta_{int(m.group(1))}"
    alias = NOMBRADAS.get(_sin_tildes(nombre))
    return alias


def _orden_clave(s: str) -> tuple:
    m = re.fullmatch(r"(d)?(\d+)(bis)?", s)
    if m:
        return (0 if not m.group(1) else 1, int(m.group(2)), m.group(3) or "", s)
    return (2, 0, "", s)


def par_key(a: str, b: str) -> str:
    return "|".join(sorted([a, b], key=_orden_clave))


def main() -> int:
    origen = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/osm_streets.json")
    destino = Path(sys.argv[2] if len(sys.argv) > 2 else "data/intersecciones.json")
    data = json.loads(origen.read_text())
    # nodo redondeado (≈ 1 m) -> set de calles
    en_nodo: dict[tuple[int, int], set[str]] = defaultdict(set)
    sin_clave: dict[str, int] = defaultdict(int)
    for el in data.get("elements") or []:
        nombre = (el.get("tags") or {}).get("name") or ""
        clave = clave_calle(nombre)
        if not clave:
            sin_clave[nombre] += 1
            continue
        for pt in el.get("geometry") or []:
            # 5 decimales ≈ 1,1 m
            key = (round(pt["lat"] * 1e5), round(pt["lon"] * 1e5))
            en_nodo[key].add(clave)

    pares: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for (ilat, ilon), calles in en_nodo.items():
        if len(calles) < 2:
            continue
        calles_l = sorted(calles)
        lat, lon = ilat / 1e5, ilon / 1e5
        for i, a in enumerate(calles_l):
            for b in calles_l[i + 1 :]:
                pares[par_key(a, b)].append((lat, lon))

    compacto: dict[str, list[dict[str, float]]] = {}
    for par, pts in pares.items():
        # Promedia puntos del mismo cruce (manos de avenida). Si hay cruces
        # distintos con el mismo número en otra localidad, se conservan todos.
        grupos: list[list[tuple[float, float]]] = []
        for lat, lon in pts:
            puesto = False
            for g in grupos:
                glat, glon = g[0]
                if abs(lat - glat) < 0.0008 and abs(lon - glon) < 0.0008:
                    g.append((lat, lon))
                    puesto = True
                    break
            if not puesto:
                grupos.append([(lat, lon)])
        compacto[par] = [
            {
                "lat": round(sum(p[0] for p in g) / len(g), 6),
                "lon": round(sum(p[1] for p in g) / len(g), 6),
            }
            for g in grupos
        ]

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(compacto, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    print(f"Escrito {destino} con {len(compacto)} intersecciones. Nombres sin clave: {len(sin_clave)}")
    # sanity
    for ej in ("7|50", "13|44", "1|60", "32|148", "8|42", "9|42", "d74|13", "7|d73"):
        print(" ", ej, compacto.get(ej))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
