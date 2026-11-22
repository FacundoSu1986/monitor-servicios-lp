#!/usr/bin/env python3
"""Monitor de servicios y eventos de Gran La Plata (diario 0221).

Lee titulares recientes de dos fuentes de 0221:
  1. el sitemap de noticias (todas las secciones, ~48 h, con fecha de publicación);
  2. la portada de la sección La Plata (lo que está destacado ahora).
Filtra los que hablan de cortes de servicios, tránsito, accidentes, incendios o
hechos policiales, guarda los nuevos en historial_lp.csv y los avisa por Telegram.
Si una fuente falla, sigue con la otra y avisa; si fallan todas, sale con error.
"""

from __future__ import annotations

import csv
import gzip
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Nombre canónico de la zona. "America/Buenos_Aires" es un alias legacy que
# Ubuntu 24.04+ ya no instala (quedó en el paquete tzdata-legacy).
TZ = ZoneInfo("America/Argentina/Buenos_Aires")
BASE_URL = "https://www.0221.com.ar"
URL = f"{BASE_URL}/la-plata"
URL_SITEMAP_NEWS = f"{BASE_URL}/sitemap-news.xml"
# Secciones de 0221 que cubren Gran La Plata (las usa también backfill.py).
SECCIONES = ("la-plata", "policiales", "berisso", "ensenada")
# Notas del sitemap publicadas antes de esto no se consideran nuevas.
VENTANA_SITEMAP = timedelta(days=3)
ARCHIVO = Path("historial_lp.csv")
CAMPOS = ["fecha", "evento", "url", "palabras"]
FORMATO_FECHA = "%Y-%m-%d %H:%M"
TIMEOUT = 30
USER_AGENT = (
    "monitor-servicios-lp/1.2 "
    "(historial local de eventos; contacto: repo GitHub FacundoSu1986/monitor-servicios-lp)"
)

# Patrones por palabra completa, sobre el titular SIN tildes (así "chocó" y
# "choco" coinciden igual). En el CSV se guarda la palabra tal como apareció.
PATRONES_ALERTA = (
    # Servicios y calle
    r"cortes?",
    r"apagon(?:es)?",
    r"edelap",
    r"absa",
    r"camuzzi",
    r"inundacion(?:es)?",
    r"transito",
    r"alerta (?:amarill[ao]|naranja|roj[ao])",  # alertas meteorológicas
    # Accidentes e incendios
    r"accidentes?",
    r"choques?",
    r"choco",
    r"chocan",
    r"chocaron",
    r"chocar",
    r"vuelcos?",
    r"volco",
    r"volcaron",
    r"atropell\w*",
    r"incendios?",
    # Policiales
    r"robos?",  # también "robó"
    r"roban",
    r"robaron",
    r"asaltos?",
    r"asaltan",
    r"asaltaron",
    r"secuestros?",
    r"persecucion(?:es)?",
    r"tiroteos?",
    r"balacera",
    r"balead[oa]s?",
    r"balean",
    r"balearon",
    r"motochorros?",
    r"entraderas?",
    r"salideras?",
    r"ladron(?:a|es|as)?",
    r"delincuentes?",
    r"a mano armada",
    r"herid[oa]s?",
    r"murio",
    r"murieron",
    r"fatales?",
    r"colision(?:es)?",
    r"despistes?",
)
# "luz", "agua" y "gas" solas dan falsos positivos ("salen a la luz"): se exigen
# con contexto de servicio en el mismo titular (los contextos son prefijos).
_CONTEXTO_SERVICIO = ("corte", "falta", "restablec", "reestablec", "restitu", "normaliz", "afectados")
PALABRAS_CON_CONTEXTO = {
    "luz": (*_CONTEXTO_SERVICIO, "sin luz", "apagon", "edelap", "cables"),
    "agua": (*_CONTEXTO_SERVICIO, "sin agua", "absa", "potable", "escasez", "crisis hidrica", "bajo agua"),
    "gas": (*_CONTEXTO_SERVICIO, "sin gas", "fuga", "escape", "camuzzi", "explosion"),
}
# Usos de "corte" que no son cortes de servicio ni de calle.
_EXCLUSIONES_RE = re.compile(
    r"(?<!\w)(?:suprema corte|corte suprema|corte interamericana"
    r"|cortes? de (?:pasto|pelo|cabello|carnes?)|al corte(?! de))(?!\w)",
    re.IGNORECASE,
)
# "la Corte" con mayúscula a mitad de titular es el tribunal (case-sensitive).
_LA_CORTE_RE = re.compile(r"(?<!\w)[Ll]a Corte(?!\w)")
_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU")
_PALABRA_RE = re.compile(r"(?<!\w)(?:" + "|".join(PATRONES_ALERTA) + r")(?!\w)", re.IGNORECASE)
_CONTEXTO_RE = {
    palabra: (
        re.compile(rf"(?<!\w){palabra}(?!\w)"),
        re.compile(r"(?<!\w)(?:" + "|".join(re.escape(c) for c in contextos) + ")"),
    )
    for palabra, contextos in PALABRAS_CON_CONTEXTO.items()
}
_ID_NOTA_RE = re.compile(r"-n(\d+)/?$")

Fila = dict[str, str]


class FuenteError(Exception):
    """Una fuente respondió pero sin titulares utilizables (p. ej. cambió el formato)."""


def _ahora() -> str:
    return datetime.now(TZ).strftime(FORMATO_FECHA)


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().lower()


def _tapar(match: re.Match[str]) -> str:
    return " " * len(match.group())


def _texto_para_buscar(texto: str) -> str:
    """Saca tildes y tapa los usos excluidos con espacios, sin mover posiciones."""
    plano = texto.translate(_SIN_TILDES)
    plano = _EXCLUSIONES_RE.sub(_tapar, plano)
    return _LA_CORTE_RE.sub(_tapar, plano)


def palabras_encontradas(texto: str) -> list[str]:
    plano = _texto_para_buscar(texto)
    halladas = {texto[m.start() : m.end()].lower() for m in _PALABRA_RE.finditer(plano)}
    plano_min = _normalizar(plano)
    for palabra, (palabra_re, contexto_re) in _CONTEXTO_RE.items():
        if palabra_re.search(plano_min) and contexto_re.search(plano_min):
            halladas.add(palabra)
    return sorted(halladas)


def clave_nota(url: str) -> str:
    """ID estable de una nota de 0221 (sufijo -nNNNNNN de la URL), o "" si no tiene."""
    m = _ID_NOTA_RE.search(urlparse(url).path)
    return m.group(1) if m else ""


def leer_historial(path: Path) -> tuple[set[str], set[str]]:
    """Titulares normalizados e IDs de nota ya guardados en el CSV."""
    titulos: set[str] = set()
    claves: set[str] = set()
    if not path.is_file():
        return titulos, claves
    with path.open(newline="", encoding="utf-8") as fh:
        for fila in csv.DictReader(fh):
            evento = (fila.get("evento") or "").strip()
            if evento:
                titulos.add(_normalizar(evento))
            clave = clave_nota((fila.get("url") or "").strip())
            if clave:
                claves.add(clave)
    return titulos, claves


def leer_eventos_previos(path: Path) -> set[str]:
    return leer_historial(path)[0]


# --- Portada (HTML) ---------------------------------------------------------


def titulares_portada(html: str, base_url: str) -> list[tuple[str, str]]:
    """Todos los titulares de una portada como (texto, url absoluta), sin filtrar."""
    soup = BeautifulSoup(html, "html.parser")
    pares: list[tuple[str, str]] = []
    nodos = soup.select("h2.news-article__title") or soup.find_all("h2")
    for nodo in nodos:
        texto = " ".join(nodo.get_text(" ", strip=True).split())
        if not texto:
            continue
        enlace = nodo.find("a") or nodo.find_parent("a")
        href = str(enlace.get("href") or "").strip() if enlace is not None else ""
        pares.append((texto, urljoin(base_url, href) if href else base_url))
    return pares


def filtrar_titulares(pares: Iterable[tuple[str, str]], fecha: str) -> list[Fila]:
    hallazgos: list[Fila] = []
    vistos: set[str] = set()
    for texto, url in pares:
        clave = _normalizar(texto)
        if clave in vistos:
            continue
        matches = palabras_encontradas(texto)
        if not matches:
            continue
        vistos.add(clave)
        hallazgos.append({"fecha": fecha, "evento": texto, "url": url, "palabras": ",".join(matches)})
    return hallazgos


def extraer_titulares(html: str, base_url: str) -> list[Fila]:
    return filtrar_titulares(titulares_portada(html, base_url), _ahora())


# --- Sitemap de noticias (XML) ----------------------------------------------


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


def filas_desde_sitemap(items: Iterable[dict], ahora: datetime, secciones: Iterable[str] = SECCIONES) -> list[Fila]:
    """Notas recientes de las secciones elegidas cuyo título tiene palabras de alerta."""
    permitidas = set(secciones)
    limite = ahora - VENTANA_SITEMAP
    filas: list[Fila] = []
    for item in items:
        titulo = " ".join((item.get("titulo") or "").split())
        if not titulo or seccion_de(item["url"]) not in permitidas:
            continue
        publicado: datetime | None = item.get("publicado")
        if publicado is not None and publicado < limite:
            continue
        matches = palabras_encontradas(titulo)
        if not matches:
            continue
        filas.append(
            {
                "fecha": (publicado or ahora).astimezone(TZ).strftime(FORMATO_FECHA),
                "evento": titulo,
                "url": item["url"],
                "palabras": ",".join(matches),
            }
        )
    return filas


# --- Descarga ---------------------------------------------------------------


def crear_sesion() -> requests.Session:
    """Sesión con reintentos acotados ante cortes de red y errores 429/5xx."""
    reintentos = Retry(
        total=3,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=False,  # un Retry-After enorme colgaría el job
        raise_on_status=False,
    )
    sesion = requests.Session()
    sesion.headers.update({"User-Agent": USER_AGENT})
    sesion.mount("https://", HTTPAdapter(max_retries=reintentos))
    return sesion


def leer_portada(sesion: requests.Session, ahora: datetime) -> list[Fila]:
    resp = sesion.get(URL, headers={"Accept": "text/html"}, timeout=TIMEOUT)
    resp.raise_for_status()
    pares = titulares_portada(resp.text, resp.url)
    if not pares:
        raise FuenteError(f"{URL} respondió sin titulares (¿cambió el HTML?)")
    return filtrar_titulares(pares, ahora.strftime(FORMATO_FECHA))


def leer_sitemap_news(sesion: requests.Session, ahora: datetime) -> list[Fila]:
    resp = sesion.get(URL_SITEMAP_NEWS, headers={"Accept": "application/xml"}, timeout=TIMEOUT)
    resp.raise_for_status()
    items = parse_sitemap_urlset(resp.content)
    if not any(i.get("titulo") for i in items):
        raise FuenteError(f"{URL_SITEMAP_NEWS} respondió sin notas con título (¿cambió el formato?)")
    return filas_desde_sitemap(items, ahora)


def recolectar(sesion: requests.Session, ahora: datetime) -> tuple[list[Fila], list[str]]:
    """Lee todas las fuentes. Devuelve coincidencias y fallas; si fallan todas, lanza FuenteError."""
    fuentes: tuple[tuple[str, Callable[[requests.Session, datetime], list[Fila]]], ...] = (
        ("sitemap de noticias", leer_sitemap_news),
        ("portada La Plata", leer_portada),
    )
    hallazgos: list[Fila] = []
    fallas: list[str] = []
    for nombre, fuente in fuentes:
        try:
            filas = fuente(sesion, ahora)
        except (requests.RequestException, FuenteError) as exc:
            logging.warning("Falló la fuente %s: %s", nombre, exc)
            fallas.append(f"{nombre}: {type(exc).__name__}: {exc}")
            continue
        except Exception as exc:  # borde por fuente: un bug de parseo no tumba a las demás
            logging.exception("Error inesperado en la fuente %s", nombre)
            fallas.append(f"{nombre}: {type(exc).__name__}: {exc}")
            continue
        logging.info("Fuente %s: %s coincidencias.", nombre, len(filas))
        hallazgos.extend(filas)
    if len(fallas) == len(fuentes):
        raise FuenteError("no se pudo leer ninguna fuente. " + " | ".join(fallas))
    return hallazgos, fallas


def nuevos_eventos(filas: Iterable[Fila], titulos_previos: set[str], claves_previas: set[str]) -> list[Fila]:
    """Descarta lo ya visto (en el CSV o en otra fuente) por titular o por ID de nota."""
    titulos = set(titulos_previos)
    claves = set(claves_previas)
    nuevos: list[Fila] = []
    for fila in filas:
        titulo = _normalizar(fila["evento"])
        clave = clave_nota(fila["url"])
        if titulo in titulos or (clave and clave in claves):
            continue
        titulos.add(titulo)
        if clave:
            claves.add(clave)
        nuevos.append(fila)
    nuevos.sort(key=lambda f: f["fecha"])
    return nuevos


def guardar_nuevos(path: Path, filas: list[Fila]) -> int:
    existe = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAMPOS)
        if not existe or path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(filas)
    return len(filas)


# --- Telegram ---------------------------------------------------------------

TELEGRAM_MAX = 4096


def _redactar(valor: object, token: str = "") -> str:
    texto = str(valor)
    if token:
        texto = texto.replace(token, "***")
    return texto


def credenciales_telegram() -> tuple[str, str] | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return token, chat_id


def _fecha_corta(fecha: str) -> str:
    try:
        return datetime.strptime(fecha, FORMATO_FECHA).strftime("%d/%m %H:%M")
    except ValueError:
        return fecha


def _bloque_evento(fila: Fila) -> str:
    titulo = fila.get("evento", "").strip()
    fecha = (fila.get("fecha") or "").strip()
    lineas = [f"• [{_fecha_corta(fecha)}] {titulo}" if fecha else f"• {titulo}"]
    url = (fila.get("url") or "").strip()
    if url:
        lineas.append(url)
    return "\n".join(lineas)


def armar_mensajes(filas: list[Fila]) -> list[str]:
    """Arma uno o más textos de Telegram, cada uno de a lo sumo 4096 caracteres."""
    if not filas:
        return []
    header = f"Monitor LP: {len(filas)} evento(s) nuevo(s)"
    mensajes: list[str] = []
    actual = header
    for fila in filas:
        bloque = _bloque_evento(fila)
        candidato = f"{actual}\n\n{bloque}"
        if len(candidato) <= TELEGRAM_MAX:
            actual = candidato
            continue
        if actual != header:
            mensajes.append(actual)
        actual = f"{header}\n\n{bloque}"
        if len(actual) > TELEGRAM_MAX:
            mensajes.append(actual[: TELEGRAM_MAX - 1] + "…")
            actual = header
    if actual != header:
        mensajes.append(actual)
    return mensajes


def enviar_telegram(texto: str, creds: tuple[str, str] | None = None) -> bool:
    """Envía un sendMessage. Si faltan credenciales, omite y lo deja en el log (sin el token)."""
    if creds is None:
        creds = credenciales_telegram()
    if creds is None:
        logging.info("Telegram no configurado (faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID); se omite la notificación.")
        return False
    token, chat_id = creds
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto, "disable_web_page_preview": True},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logging.error("No se pudo enviar el mensaje a Telegram: %s", _redactar(exc, token))
        return False


def notificar_eventos(filas: list[Fila]) -> None:
    if not filas:
        return
    creds = credenciales_telegram()
    if creds is None:
        logging.info("Telegram no configurado (faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID); se omite la notificación.")
        return
    for mensaje in armar_mensajes(filas):
        enviar_telegram(mensaje, creds)


def notificar_error(exc: BaseException) -> None:
    creds = credenciales_telegram()
    if creds is None:
        logging.info("Telegram no configurado (faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID); se omite la notificación.")
        return
    detalle = _redactar(exc, creds[0])
    texto = f"Monitor LP: falló el scrapeo.\n{type(exc).__name__}: {detalle}"
    enviar_telegram(texto[:TELEGRAM_MAX], creds)


def notificar_fallas_parciales(fallas: list[str]) -> None:
    creds = credenciales_telegram()
    if creds is None:
        logging.info("Telegram no configurado (faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID); se omite la notificación.")
        return
    texto = "Monitor LP: aviso, falló una fuente (el resto se leyó y guardó).\n" + "\n".join(fallas)
    enviar_telegram(_redactar(texto, creds[0])[:TELEGRAM_MAX], creds)


# --- Orquestación -----------------------------------------------------------


def scraping_la_plata(ahora: datetime | None = None) -> int:
    try:
        ahora = ahora or datetime.now(TZ)
        sesion = crear_sesion()
        hallazgos, fallas = recolectar(sesion, ahora)
        titulos_previos, claves_previas = leer_historial(ARCHIVO)
        nuevos = nuevos_eventos(hallazgos, titulos_previos, claves_previas)

        if nuevos:
            guardar_nuevos(ARCHIVO, nuevos)
            logging.info("Guardados %s eventos nuevos (de %s coincidencias).", len(nuevos), len(hallazgos))
            for fila in nuevos:
                logging.info(" + %s | %s", fila["fecha"], fila["evento"])
            try:
                from enriquecer import enriquecer_nuevos

                enriquecer_nuevos(sesion, nuevos)
            except Exception:
                logging.exception("No se pudo enriquecer las notas nuevas (el historial sí se guardó).")
            notificar_eventos(nuevos)
        else:
            logging.info("Sin eventos nuevos (coincidencias: %s).", len(hallazgos))
        if fallas:
            notificar_fallas_parciales(fallas)
        return len(nuevos)
    except Exception as exc:
        # Avisa antes de relanzar para que el job de Actions quede en failure.
        notificar_error(exc)
        raise


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        scraping_la_plata()
    except (FuenteError, requests.RequestException) as exc:
        logging.error("No se pudo leer 0221: %s", exc)
        return 1
    except Exception as exc:
        logging.exception("Error inesperado: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
