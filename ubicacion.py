#!/usr/bin/env python3
"""Extracción de lugar en Gran La Plata y geocodificación local de esquinas."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"

# Calles numeradas habituales del casco y alrededores. Sin prefijo "calle/avenida"
# no aceptamos números fuera de este rango (evita edades, horarios, km, etc.).
NUM_MIN, NUM_MAX = 1, 1300
# Caja aproximada del casco urbano de La Plata (para desambiguar "7 y 50").
CASCO = (-34.940, -34.900, -57.980, -57.925)

_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")
_NO = r"(?![\s]*?(?:años?|meses|dias|horas?|minutos?|puntos?|goles?|semanas?|victimas?|heridos?|muertos?|fallecidos?|personas?|casos?))"
_NUM = r"(\d{1,4}(?:\s*bis)?)\b"
_VIA = r"(?:calle|avenida|av\.?|diag(?:onal)?)"

# "de 7 y 50 años", "entre las 8 y las 12"
_FALSO_EDAD = re.compile(rf"(?:de|tiene[n]?)\s+{_NUM}\s+y\s+{_NUM}\s+años?", re.I)
_FALSO_HORA = re.compile(
    r"(?:a\s+las|entre\s+las|desde\s+las|hasta\s+las)\s+\d{1,2}\s+y\s+(?:las\s+)?\d{1,2}",
    re.I,
)

# "avenida 32 entre 148 y 148 bis" / "en 13 entre 40 y 41"
_CUADRA = re.compile(
    rf"(?:{_VIA}\s+)?{_NUM}\s*,?\s+entre\s+{_NUM}\s+y\s+{_NUM}",
    re.I,
)
# "7 y 50", "calle 42 y 8", "avenida 32 y 148"
_ESQUINA = re.compile(
    rf"(?:{_VIA}\s+)?{_NUM}\s+y\s+(?:{_VIA}\s+)?{_NUM}{_NO}",
    re.I,
)
_DIAGONAL = re.compile(r"\bdiag(?:onal)?\s+(\d{1,3})\b", re.I)
_AVENIDA = re.compile(r"\b(?:avenida|av\.)\s+(\d{1,4})\b", re.I)
_CAMINO = re.compile(
    r"\b(?:camino\s+(?:parque\s+)?centenario|camino\s+general\s+belgrano|parque\s+centenario)\b",
    re.I,
)
_RUTA_KM = re.compile(r"\bruta\s*(\d{1,3})\s*(?:km\.?|kilometro)\s*(\d{1,4})\b", re.I)
_RUTA = re.compile(r"\bruta\s*(?:provincial\s+|nacional\s+)?(?:n[°ºo]?\s*)?(\d{1,3})\b", re.I)
_KM_SUELTO = re.compile(r"(?:a la altura del\s+)?(?:km\.?|kil[oó]metro)\s*(\d{1,4})\b", re.I)
_CORTE_CONTEXTO = re.compile(
    r"(?:las v[ií]ctimas del tr[aá]nsito en la plata|te puede interesar|"
    r"noticias relacionadas|otras notas|mir[aá] tambi[eé]n)",
    re.I,
)
_CUADRA_ALTURA = re.compile(
    rf"(?:{_VIA}\s+){_NUM}\s+(?:al|nro\.?|n[°º]|numero)\s*(\d{{1,5}})\b",
    re.I,
)


def _plano(texto: str) -> str:
    return texto.translate(_SIN_TILDES)


def _num_clave(token: str) -> str:
    token = token.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+)(bis)?", token)
    if not m:
        return token
    return f"{int(m.group(1))}bis" if m.group(2) else str(int(m.group(1)))


def _num_ok(token: str, estricto: bool) -> bool:
    m = re.match(r"(\d+)", token.replace(" ", ""))
    if not m:
        return False
    n = int(m.group(1))
    tope = NUM_MAX if not estricto else 250
    return NUM_MIN <= n <= tope


def en_casco(lat: float, lon: float) -> bool:
    sur, nor, oeste, este = CASCO
    return sur <= lat <= nor and oeste <= lon <= este


@lru_cache(maxsize=1)
def _intersecciones() -> dict[str, list[dict[str, float]]]:
    path = DATA / "intersecciones.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _barrios() -> list[dict]:
    path = DATA / "barrios.json"
    if not path.is_file():
        return []
    datos = json.loads(path.read_text(encoding="utf-8"))
    filas = []
    for clave, info in datos.items():
        aliases = info.get("aliases") or [info["nombre"]]
        filas.append(
            {
                "id": clave,
                "nombre": info["nombre"],
                "tipo": info.get("tipo") or "localidad",
                "lat": info.get("lat"),
                "lon": info.get("lon"),
                "aliases": sorted(aliases, key=len, reverse=True),
            }
        )
    filas.sort(key=lambda f: max(len(a) for a in f["aliases"]), reverse=True)
    return filas


def par_key(a: str, b: str) -> str:
    def orden(s: str) -> tuple:
        m = re.fullmatch(r"(d)?(\d+)(bis)?", s)
        if m:
            return (0 if not m.group(1) else 1, int(m.group(2)), m.group(3) or "", s)
        return (2, 0, "", s)

    return "|".join(sorted([a, b], key=orden))


def _tapar(texto: str, regex: re.Pattern[str]) -> str:
    return regex.sub(lambda m: " " * len(m.group()), texto)


def _puntos_par(a: str, b: str) -> list[dict[str, float]]:
    return list(_intersecciones().get(par_key(a, b)) or [])


def elegir_punto(
    puntos: list[dict[str, float]],
    pista: tuple[float, float] | None = None,
    preferir_casco: bool = True,
) -> dict[str, float] | None:
    if not puntos:
        return None
    if pista is not None:
        plat, plon = pista
        return min(puntos, key=lambda p: (p["lat"] - plat) ** 2 + (p["lon"] - plon) ** 2)
    if preferir_casco:
        en = [p for p in puntos if en_casco(p["lat"], p["lon"])]
        if en:
            return en[0]
    return puntos[0]


def buscar_barrios(texto: str) -> list[dict]:
    """Menciones de barrio/localidad (sin contar el genérico 'La Plata')."""
    plano = _plano(texto).lower()
    hallados: list[dict] = []
    cubierto = [False] * len(plano)
    for b in _barrios():
        for alias in b["aliases"]:
            al = _plano(alias).lower()
            if al == "romero":
                pat = re.compile(r"(?<!melchor\s)(?<!\w)romero(?!\w)", re.I)
            elif al == "olmos":
                pat = re.compile(r"(?<!lisandro\s)(?<!\w)olmos(?!\w)", re.I)
            elif al in {"hernandez", "hernández"}:
                pat = re.compile(r"(?<!jose\s)(?<!josé\s)(?<!\w)hern[aá]ndez(?!\w)", re.I)
            else:
                pat = re.compile(rf"(?<!\w){re.escape(al)}(?!\w)", re.I)
            for m in pat.finditer(plano):
                if any(cubierto[m.start() : m.end()]):
                    continue
                pref = plano[max(0, m.start() - 60) : m.start()]
                if re.search(
                    r"(?:domiciliad[oa]s?|oriund[oa]s?|residentes?|viven?)\s+(?:en|de)\s+$",
                    pref,
                    re.I,
                ) or re.search(r"hospital\b.{0,50}\bde\s+$", pref, re.I):
                    continue
                for i in range(m.start(), m.end()):
                    cubierto[i] = True
                hallados.append({**b, "span": (m.start(), m.end())})
                break
    hallados.sort(key=lambda h: h["span"][0])
    return hallados


def _pista_barrio(texto: str) -> tuple[float, float] | None:
    for b in buscar_barrios(texto):
        if b.get("lat") is not None and b.get("lon") is not None:
            if b["id"] == "casco_urbano":
                continue
            return (float(b["lat"]), float(b["lon"]))
    return None


def _geocodificar_esquina(a: str, b: str, texto: str) -> dict[str, float] | None:
    return elegir_punto(_puntos_par(a, b), pista=_pista_barrio(texto))


def _geocodificar_cuadra(eje: str, a: str, b: str, texto: str) -> dict[str, float] | None:
    pista = _pista_barrio(texto)
    p1 = elegir_punto(_puntos_par(eje, a), pista=pista)
    p2 = elegir_punto(_puntos_par(eje, b), pista=pista or ((p1["lat"], p1["lon"]) if p1 else None))
    if p1 and p2:
        # Si quedaron en grillas distintas, acercamos p2 a p1.
        if abs(p1["lat"] - p2["lat"]) > 0.01 or abs(p1["lon"] - p2["lon"]) > 0.01:
            p2 = elegir_punto(_puntos_par(eje, b), pista=(p1["lat"], p1["lon"])) or p2
        return {"lat": round((p1["lat"] + p2["lat"]) / 2, 6), "lon": round((p1["lon"] + p2["lon"]) / 2, 6)}
    return p1 or p2


def _lead(texto: str) -> str:
    """Copete y primeros párrafos (~1600 caracteres), recortando 'más noticias'."""
    m = _CORTE_CONTEXTO.search(texto)
    base = texto[: m.start()] if m else texto
    if len(base) <= 1600:
        return base
    cut = base[:1600]
    nl = cut.rfind("\n")
    if nl >= 800:
        return cut[:nl]
    return cut


def _lugar_ruta(plano: str) -> dict | None:
    """Ruta N km M juntos, o 'Ruta 6' + 'kilómetro 42' a menos de 600 caracteres."""
    m = _RUTA_KM.search(plano)
    if m:
        return {
            "lugar_texto": f"Ruta {int(m.group(1))} km {int(m.group(2))}",
            "precision": "cuadra",
            "lat": None,
            "lon": None,
        }
    rutas = list(_RUTA.finditer(plano))
    kms = list(_KM_SUELTO.finditer(plano))
    if rutas and kms:
        n = int(rutas[0].group(1))
        anclas = [r for r in rutas if int(r.group(1)) == n] or [rutas[0]]
        def _dist(k) -> int:
            return min(abs(k.start() - r.start()) for r in anclas)
        k0 = min(kms, key=_dist)
        if _dist(k0) <= 1200:
            return {
                "lugar_texto": f"Ruta {n} km {int(k0.group(1))}",
                "precision": "cuadra",
                "lat": None,
                "lon": None,
            }
    if rutas:
        return {
            "lugar_texto": f"Ruta {int(rutas[0].group(1))}",
            "precision": "cuadra",
            "lat": None,
            "lon": None,
        }
    return None


def _extraer_en(texto: str, *, ruta_antes_de_barrio: bool = False) -> dict:
    vacio = {"lugar_texto": "", "precision": "sin_lugar", "lat": None, "lon": None}
    if not texto or not texto.strip():
        return vacio

    plano = _plano(texto)
    plano = _tapar(plano, _FALSO_EDAD)
    plano = _tapar(plano, _FALSO_HORA)

    # 1) cuadra: "32 entre 148 y 148 bis"
    m = _CUADRA.search(plano)
    if m and all(_num_ok(m.group(i), estricto=False) for i in (1, 2, 3)):
        eje, a, b = _num_clave(m.group(1)), _num_clave(m.group(2)), _num_clave(m.group(3))
        punto = _geocodificar_cuadra(eje, a, b, texto)
        lugar = f"{eje} entre {a} y {b}".replace("bis", " bis")
        return {
            "lugar_texto": lugar,
            "precision": "cuadra",
            "lat": punto["lat"] if punto else None,
            "lon": punto["lon"] if punto else None,
        }

    # 2) esquina: "7 y 50"
    for m in _ESQUINA.finditer(plano):
        raw = m.group(0)
        a, b = _num_clave(m.group(1)), _num_clave(m.group(2))
        prefijo = bool(re.search(r"(?:calle|avenida|av\.?|diag)", raw, re.I))
        conocido = bool(_puntos_par(a, b))
        if not conocido and (not _num_ok(a, estricto=not prefijo) or not _num_ok(b, estricto=not prefijo)):
            continue
        if a.rstrip("bis") == b.rstrip("bis") and ("bis" in a or "bis" in b):
            continue
        pref = plano[max(0, m.start() - 40) : m.start()]
        if re.search(r"calles\s+$", pref, re.I):
            continue
        if re.search(r"(?:vivi[aá]n?|domiciliad[oa]s?)\s+en(?:\s+la\s+zona\s+de)?\s+$", pref, re.I):
            continue
        punto = _geocodificar_esquina(a, b, texto)
        return {
            "lugar_texto": f"{a} y {b}".replace("bis", " bis"),
            "precision": "esquina",
            "lat": punto["lat"] if punto else None,
            "lon": punto["lon"] if punto else None,
        }

    # 3) altura: "calle 12 al 1500"
    m = _CUADRA_ALTURA.search(plano)
    if m and _num_ok(m.group(1), estricto=False):
        return {
            "lugar_texto": f"{_num_clave(m.group(1))} al {m.group(2)}",
            "precision": "cuadra",
            "lat": None,
            "lon": None,
        }

    # 4) ruta km (juntos o cercanos)
    ruta = _lugar_ruta(plano)
    if ruta and (ruta["lugar_texto"].startswith("Ruta ") and " km " in ruta["lugar_texto"]):
        return ruta

    # 5) camino / diagonal / avenida sueltos (sin coordenada)
    m = _CAMINO.search(plano)
    if m:
        nombre = re.sub(r"\s+", " ", m.group(0)).strip().title().replace("De", "de")
        return {"lugar_texto": nombre, "precision": "cuadra", "lat": None, "lon": None}
    m = _DIAGONAL.search(plano)
    if m:
        return {"lugar_texto": f"Diagonal {int(m.group(1))}", "precision": "cuadra", "lat": None, "lon": None}

    if ruta_antes_de_barrio and ruta:
        return ruta

    # 6) barrio / localidad (sin lat/lon: no se pinta como punto)
    barrios = buscar_barrios(texto)
    if barrios:
        b = barrios[0]
        return {"lugar_texto": b["nombre"], "precision": b["tipo"], "lat": None, "lon": None}

    if ruta:
        return ruta
    m = _AVENIDA.search(plano)
    if m and _num_ok(m.group(1), estricto=False):
        return {"lugar_texto": f"Avenida {int(m.group(1))}", "precision": "cuadra", "lat": None, "lon": None}

    return vacio


def extraer_lugar(texto: str) -> dict:
    """Devuelve lugar_texto, precision, lat, lon (lat/lon solo en esquina o cuadra).

    Prioriza el copete y los primeros párrafos (ruta/km incluidos) frente a
    menciones de contexto más abajo en la nota.
    """
    vacio = {"lugar_texto": "", "precision": "sin_lugar", "lat": None, "lon": None}
    if not texto or not texto.strip():
        return vacio
    lead = _lead(texto)
    hallado = _extraer_en(lead, ruta_antes_de_barrio=True)
    if hallado["precision"] != "sin_lugar":
        return hallado
    if lead.strip() == texto.strip():
        return hallado
    return _extraer_en(texto, ruta_antes_de_barrio=False)
