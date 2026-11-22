#!/usr/bin/env python3
"""Clasificación por reglas: tipo de nota, categoría, gravedad y fecha del hecho."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from bot_lp import TZ, _normalizar, parse_fecha_iso

_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")

_DIAS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}

# Se evalúan en orden. El primero que matchea gana.
_TIPO = (
    (
        "seguimiento_judicial",
        re.compile(
            r"(?<!\w)(?:condenaron|condena(?:ron)?|juicio oral|indagatoria|procesaron|"
            r"imputaron|sentencia|tribunal(?:es)?|apelaci[oó]n|veredicto|"
            r"avanza la causa|seguira en libertad|fue procesad[oa]|absolvi[oó]|"
            r"el juicio a|juicio a la)\b",
            re.I,
        ),
    ),
    (
        "anuncio",
        re.compile(
            r"(?<!\w)(?:anunci[oó]|anuncian|realizara|realizaran|podria(?:n)? haber|"
            r"esta previsto|estan previstas?|cronograma|tareas de mantenimiento|"
            r"trabajos de mantenimiento|programad[oa]s?|se realizaran trabajos|"
            r"podrian producirse cortes|podria haber cortes)\b",
            re.I,
        ),
    ),
    (
        "opinion_politica",
        re.compile(
            r"(?<!\w)(?:piden|proponen|cuestionan|el debate|debate por|la politica|"
            r"un informe (?:revelo|senalo)|segun un informe|descentralizar|"
            r"editorial|columna de opinion|es el municipio con mas)\b",
            re.I,
        ),
    ),
)

_CATEGORIA = (
    ("homicidio_balacera", re.compile(r"(?<!\w)(?:homicidio|asesinat[oa]|mataron|lo mataron|la mataron|tiroteos?|balaceras?|balead[oa]s?|balearon|crimen)\b", re.I)),
    ("incendio", re.compile(r"(?<!\w)incendios?\b", re.I)),
    ("corte_luz", re.compile(r"(?<!\w)(?:edelap|cortes? de luz|sin luz|apagon(?:es)?|falta de luz)\b", re.I)),
    ("corte_agua", re.compile(r"(?<!\w)(?:absa|cortes? de agua|sin agua|falta de agua)\b", re.I)),
    ("corte_gas", re.compile(r"(?<!\w)(?:camuzzi|cortes? de gas|sin gas|fuga de gas|escape de gas)\b", re.I)),
    ("temporal_inundacion", re.compile(r"(?<!\w)(?:inundacion(?:es)?|temporal(?:es)?|alerta (?:amarill[ao]|naranja|roj[ao])|tormenta(?:s)? fuertes?)\b", re.I)),
    ("accidente_vial", re.compile(r"(?<!\w)(?:accidentes?(?: de transito)?|choques?|choco|chocaron|vuelcos?|volco|despiste|colision(?:es)?|atropell\w*)\b", re.I)),
    ("robo", re.compile(r"(?<!\w)(?:robos?|robo|asaltos?|motochorros?|entraderas?|salideras?|hurto|a mano armada|ladrones|delincuentes?)\b", re.I)),
    ("corte_calle_piquete", re.compile(r"(?<!\w)(?:piquetes?|cortes? de (?:calle|transito|ruta)|desvios? de transito|movilizacion|cortaron la (?:ruta|avenida|calle)|cortes y desvios)\b", re.I)),
)

_FATAL = re.compile(r"(?<!\w)(?:murio|murieron|muert[oa]s?|fallec\w*|fatal(?:es)?|perdio la vida|perdieron la vida|deceso)\b", re.I)
_HERIDOS = re.compile(r"(?<!\w)(?:herid[oa]s?|internad[oa]s?|hospitalizad[oa]s?|lesiones|estado (?:reservado|critico|grave)|riesgo de vida)\b", re.I)
_ARMA = re.compile(r"(?<!\w)(?:a mano armada|armas?|entraderas?|(?<!pedido de )secuestros?(?!\s+activo)|rehenes|cuchillo|punal|apunal)\b", re.I)
_MOTOCHORRO = re.compile(r"(?<!\w)motochorros?\b", re.I)
_AMPLIO = re.compile(r"(?<!\w)(?:miles de|distintos barrios|varios barrios|cuatro sectores|varios sectores|la region|masivas?|masivo|ampli[oa]s? zona)\b", re.I)

_ANCHE = re.compile(r"(?<!\w)(?:anoche|ayer a la noche|la noche del)\b", re.I)
_ESTA_MADRUGADA = re.compile(r"(?<!\w)(?:esta madrugada|hoy a la madrugada|en la madrugada|durante la madrugada)\b", re.I)
_ESTA_MANANA = re.compile(r"(?<!\w)(?:esta manana|hoy a la manana|esta mañana|hoy a la mañana)\b", re.I)
_ESTA_TARDE = re.compile(r"(?<!\w)(?:esta tarde|hoy a la tarde)\b", re.I)
_DIA_REL = re.compile(
    r"(?<!\w)(?:el|este|este pasado|el pasado|el ultimo|ultimo)?\s*(lunes|martes|miercoles|jueves|viernes|sabado|domingo)"
    r"(?:\s+a la\s+(madrugada|manana|mañana|tarde|noche))?",
    re.I,
)


def _plano(texto: str) -> str:
    return texto.translate(_SIN_TILDES)


def clasificar_tipo(titulo: str, cuerpo: str = "") -> str:
    texto = _plano(f"{titulo}\n{cuerpo}")
    for tipo, regex in _TIPO:
        if regex.search(texto):
            return tipo
    return "hecho"


def clasificar_categoria(titulo: str, cuerpo: str = "") -> str:
    # El titular pesa más: si ya define categoría, se usa.
    for origen in (_plano(titulo), _plano(f"{titulo}\n{cuerpo}")):
        for cat, regex in _CATEGORIA:
            if regex.search(origen):
                return cat
    return "otro"


def clasificar_gravedad(categoria: str, titulo: str, cuerpo: str = "") -> str:
    texto = _plano(f"{titulo}\n{cuerpo}")
    if categoria == "accidente_vial":
        if _FATAL.search(texto):
            return "fatal"
        if _HERIDOS.search(texto):
            return "heridos"
        return "danos"
    if categoria in {"robo", "homicidio_balacera"}:
        if categoria == "homicidio_balacera" or _FATAL.search(texto):
            return "fatal" if _FATAL.search(texto) or categoria == "homicidio_balacera" else "arma_o_entradera"
        if _ARMA.search(texto):
            return "arma_o_entradera"
        if _MOTOCHORRO.search(texto):
            return "motochorro"
        return "hurto"
    if categoria.startswith("corte_") or categoria == "corte_calle_piquete":
        return "amplio" if _AMPLIO.search(texto) else "puntual"
    if categoria == "incendio":
        if _FATAL.search(texto) or _HERIDOS.search(texto):
            return "alta"
        if re.search(r"(?<!\w)(?:viviendas?|casas?|pension|hogar|edificio)", texto, re.I):
            return "media"
        return "baja"
    if _FATAL.search(texto):
        return "alta"
    if _HERIDOS.search(texto):
        return "media"
    return "baja"


def _hora_franja(franja: str | None, base: datetime) -> datetime:
    franja = _plano(franja or "").lower()
    horas = {"madrugada": 3, "manana": 9, "mañana": 9, "tarde": 16, "noche": 22}
    return base.replace(hour=horas.get(franja, base.hour), minute=0, second=0, microsecond=0)


def inferir_fecha_hecho(titulo: str, cuerpo: str, fecha_pub: datetime | None) -> tuple[datetime | None, bool]:
    """Devuelve (fecha_hecho, es_publicacion). Si no hay pista temporal, usa la publicación."""
    if fecha_pub is None:
        return None, True
    pub = fecha_pub.astimezone(TZ)
    texto = _plano(f"{titulo}\n{cuerpo}")

    if _ANCHE.search(texto):
        return _hora_franja("noche", pub - timedelta(days=1)), False
    if _ESTA_MADRUGADA.search(texto):
        return _hora_franja("madrugada", pub), False
    if _ESTA_MANANA.search(texto):
        return _hora_franja("manana", pub), False
    if _ESTA_TARDE.search(texto):
        return _hora_franja("tarde", pub), False

    m = _DIA_REL.search(texto)
    if m:
        objetivo = _DIAS[_plano(m.group(1)).lower()]
        pref = _plano(m.group(0)).lower()
        pasado = any(p in pref for p in ("pasado", "ultimo"))
        if objetivo == pub.weekday() and not (pasado and pref.startswith("el pasado")):
            dia = pub
        elif pasado or objetivo < pub.weekday():
            delta = (pub.weekday() - objetivo) % 7 or 7
            dia = pub - timedelta(days=delta)
        else:
            # "este jueves" publicado el miércoles: el que viene (anuncios y hechos).
            dia = pub + timedelta(days=objetivo - pub.weekday())
        return _hora_franja(m.group(2), dia), False

    return pub, True


def clasificar(titulo: str, cuerpo: str = "", fecha_pub: datetime | str | None = None) -> dict:
    if isinstance(fecha_pub, str):
        fecha_pub = parse_fecha_iso(fecha_pub) or datetime.strptime(fecha_pub, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    tipo = clasificar_tipo(titulo, cuerpo)
    categoria = clasificar_categoria(titulo, cuerpo)
    gravedad = clasificar_gravedad(categoria, titulo, cuerpo)
    hecho, es_pub = inferir_fecha_hecho(titulo, cuerpo, fecha_pub)
    return {
        "tipo_nota": tipo,
        "categoria": categoria,
        "gravedad": gravedad,
        "fecha_hecho": hecho.strftime("%Y-%m-%d %H:%M") if hecho else "",
        "fecha_hecho_es_publicacion": es_pub,
    }
