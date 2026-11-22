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
    ("corte_luz", re.compile(r"(?<!\w)(?:edelap|cortes?(?:\s+\w+){0,2}\s+de luz|sin luz|apagon(?:es)?|falta de luz|servicio de luz|recup\w* la luz|restituy\w* (?:el )?servicio)\b", re.I)),
    ("corte_agua", re.compile(r"(?<!\w)(?:absa|cortes?(?:\s+\w+){0,2}\s+de agua|sin agua|falta de agua)\b", re.I)),
    ("corte_gas", re.compile(r"(?<!\w)(?:camuzzi|cortes? de gas|sin gas|fuga de gas|escape de gas)\b", re.I)),
    ("temporal_inundacion", re.compile(r"(?<!\w)(?:inundacion(?:es)?|temporal(?:es)?|alerta (?:amarill[ao]|naranja|roj[ao])|tormenta(?:s)? fuertes?)\b", re.I)),
    ("accidente_vial", re.compile(r"(?<!\w)(?:accidentes?(?: de transito)?|choques?|choco|chocaron|vuelcos?|volco|despiste|colision(?:es)?|atropell\w*)\b", re.I)),
    ("robo", re.compile(r"(?<!\w)(?:robos?|robo|asaltos?|motochorros?|entraderas?|salideras?|hurto|a mano armada|ladrones|delincuentes?)\b", re.I)),
    ("corte_calle_piquete", re.compile(r"(?<!\w)(?:piquetes?|cortes? de (?:calle|transito|ruta)|desvios? de transito|movilizacion|cortaron la (?:ruta|avenida|calle)|cortes y desvios)\b", re.I)),
)

# Incendios que no son un hecho local (otra región, ficción, simulacro, alerta genérica).
_INCENDIO_AJENO = re.compile(
    r"(?:patagonia|incendios? en el sur|el fuego en(?: la)? patagonia|"
    r"combat(?:e|ir|ieron) (?:los )?incendios en|"
    r"invocar lluvias|"
    r"estacion constitucion|(?<!\w)constitucion(?!\w)|tren roca|"
    r"filmaci[oó]n de netflix|rodaje de (?:la )?(?:miniserie |nueva serie )?de netflix|"
    r"asi sera la filmacion|"
    r"simulacro de incendio|"
    r"bombero drone|bombero dron|"
    r"riesgo extremo de incendios|"
    r"(?<!\w)brandsen(?!\w))",
    re.I,
)
_ACCIDENTE_EN_TITULO = re.compile(
    r"(?<!\w)(?:choques?|choco|chocaron|vuelcos?|volco|despiste|colision(?:es)?|"
    r"accidente(?:s)? de transito|motociclista)\b",
    re.I,
)
# "el incendio fue un accidente" no es un hecho vial.
_ACCIDENTE_NO_VIAL = re.compile(r"incendio.{0,40}fue un accidente|fue un accidente.{0,40}incendio", re.I)
_AGUA_EN_TITULO = re.compile(
    r"(?<!\w)(?:absa|sin agua|falta de agua|falt\w* agua|cortes?(?:\s+\w+){0,2}\s+de agua)\b",
    re.I,
)
_LUZ_EN_TITULO = re.compile(
    r"(?<!\w)(?:edelap|sin luz|apagon(?:es)?|falta de luz|cortes?(?:\s+\w+){0,2}\s+de luz|"
    r"recup[eé]r\w* la luz|servicio de luz|generadores?)\b",
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
    """El titular pesa más. El incendio no pisa un choque ni un corte de luz del titular,
    y se descartan incendios ajenos a Gran La Plata (Patagonia, CABA, ficción, simulacros)."""
    tit = _plano(titulo)
    full = _plano(f"{titulo}\n{cuerpo}")

    def _permite(cat: str, origen: str, es_titulo: bool) -> bool:
        if cat == "incendio":
            if _INCENDIO_AJENO.search(full):
                return False
            if _ACCIDENTE_EN_TITULO.search(tit) and not _ACCIDENTE_NO_VIAL.search(tit):
                return False
            if _LUZ_EN_TITULO.search(tit) or _AGUA_EN_TITULO.search(tit):
                return False
        if cat == "accidente_vial" and _ACCIDENTE_NO_VIAL.search(origen):
            return False
        return True

    for origen, es_titulo in ((tit, True), (full, False)):
        for cat, regex in _CATEGORIA:
            if regex.search(origen) and _permite(cat, origen, es_titulo):
                return cat
    return "otro"



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
