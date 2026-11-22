#!/usr/bin/env python3
"""Monitor de titulares de 0221 (La Plata): guarda eventos nuevos en historial_lp.csv."""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("America/Buenos_Aires")
URL = "https://www.0221.com.ar/la-plata"
ARCHIVO = Path("historial_lp.csv")
CAMPOS = ["fecha", "evento", "url", "palabras"]
TIMEOUT = 30
USER_AGENT = (
    "monitor-servicios-lp/1.1 "
    "(historial local de eventos; contacto: repo GitHub FacundoSu1986/monitor-servicios-lp)"
)

# "luz" y "agua" solas generan falsos positivos (p.ej. "salen a la luz").
# Esas dos se exigen con contexto de servicio; el resto coincide por palabra completa.
PALABRAS_ALERTA = [
    "corte",
    "cortes",
    "choque",
    "chocó",
    "choco",
    "incendio",
    "edelap",
    "absa",
    "inundación",
    "inundacion",
    "tránsito",
    "transito",
    "robo",
    "secuestro",
    "persecución",
    "persecucion",
    "tiroteo",
    "motochorro",
    "asalto",
    "policía",
    "policia",
]
PALABRAS_CON_CONTEXTO = {
    "luz": ("corte", "cortes", "sin luz", "falta de luz", "apagón", "apagon", "edelap"),
    "agua": ("corte", "cortes", "sin agua", "falta de agua", "absa", "potable"),
}

_PALABRA_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(p) for p in PALABRAS_ALERTA) + r")(?!\w)",
    re.IGNORECASE,
)


def _ahora() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M")


def _normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip().lower()


def palabras_encontradas(texto: str) -> list[str]:
    halladas = {m.group(1).lower() for m in _PALABRA_RE.finditer(texto)}
    under = _normalizar(texto)
    for palabra, contextos in PALABRAS_CON_CONTEXTO.items():
        if re.search(rf"(?<!\w){re.escape(palabra)}(?!\w)", under) and any(c in under for c in contextos):
            halladas.add(palabra)
    return sorted(halladas)


def leer_eventos_previos(path: Path) -> set[str]:
    vistos: set[str] = set()
    if not path.is_file():
        return vistos
    with path.open(newline="", encoding="utf-8") as fh:
        lector = csv.DictReader(fh)
        for fila in lector:
            evento = (fila.get("evento") or "").strip()
            if evento:
                vistos.add(_normalizar(evento))
    return vistos


def extraer_titulares(html: str, base_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    hallazgos: list[dict[str, str]] = []
    vistos_en_pagina: set[str] = set()

    nodos = soup.select("h2.news-article__title") or soup.find_all("h2")
    for nodo in nodos:
        texto = " ".join(nodo.get_text(" ", strip=True).split())
        if not texto:
            continue
        clave = _normalizar(texto)
        if clave in vistos_en_pagina:
            continue
        matches = palabras_encontradas(texto)
        if not matches:
            continue
        vistos_en_pagina.add(clave)
        enlace = nodo.find("a") or nodo.find_parent("a")
        href = enlace["href"].strip() if enlace and enlace.has_attr("href") else ""
        if href and href.startswith("/"):
            href = "https://www.0221.com.ar" + href
        hallazgos.append(
            {
                "fecha": _ahora(),
                "evento": texto,
                "url": href or base_url,
                "palabras": ",".join(matches),
            }
        )
    return hallazgos


def guardar_nuevos(path: Path, filas: list[dict[str, str]]) -> int:
    existe = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAMPOS)
        if not existe or path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(filas)
    return len(filas)


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


def _bloque_evento(fila: dict[str, str]) -> str:
    lineas = [f"• {fila.get('evento', '').strip()}"]
    url = (fila.get("url") or "").strip()
    if url:
        lineas.append(url)
    return "\n".join(lineas)


def armar_mensajes(filas: list[dict[str, str]]) -> list[str]:
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
    except Exception as exc:
        logging.error("No se pudo enviar el mensaje a Telegram: %s", _redactar(exc, token))
        return False


def notificar_eventos(filas: list[dict[str, str]]) -> None:
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


def scraping_la_plata() -> int:
    logging.info("Consultando %s", URL)
    try:
        response = requests.get(
            URL,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        hallazgos = extraer_titulares(response.text, response.url)
        previos = leer_eventos_previos(ARCHIVO)
        nuevos = [h for h in hallazgos if _normalizar(h["evento"]) not in previos]

        if nuevos:
            guardar_nuevos(ARCHIVO, nuevos)
            logging.info("Guardados %s eventos nuevos (de %s coincidencias en la página).", len(nuevos), len(hallazgos))
            notificar_eventos(nuevos)
        else:
            logging.info("Sin eventos nuevos (coincidencias en la página: %s).", len(hallazgos))
        return len(nuevos)
    except Exception as exc:
        # Avisa antes de relanzar para que el job de Actions quede en failure.
        notificar_error(exc)
        raise


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        scraping_la_plata()
    except requests.RequestException as exc:
        logging.error("Error de red o HTTP: %s", exc)
        return 1
    except Exception as exc:
        logging.exception("Error inesperado: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
