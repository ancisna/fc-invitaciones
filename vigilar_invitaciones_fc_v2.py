"""
Vigilante de invitaciones de ForoCoches - Telegram + Newsletter (Gmail)
-------------------------------------------------------------------------
Vigila DOS fuentes:
  1. El canal público de Telegram (t.me/s/forocoches_oficial)
  2. Tu bandeja de Gmail, buscando correos nuevos de la newsletter de
     ForoCoches (remitente newsletter.forocoches.com) que lleguen sin leer

Cuando encuentra algo relevante en cualquiera de las dos, te avisa al
instante por Telegram a través de tu propio bot.

Variables de entorno necesarias (se configuran como Secrets en GitHub):
  BOT_TOKEN     -> token de tu bot de Telegram (de BotFather)
  CHAT_ID       -> tu chat_id de Telegram
  GMAIL_USER    -> tu correo de Gmail completo (ej: tunombre@gmail.com)
  GMAIL_APP_PW  -> la "contraseña de aplicación" generada en Gmail
"""

import time
import re
import json
import os
import sys
import imaplib
import email
from email.header import decode_header
from html import unescape

import requests

# ------------------- CONFIGURACIÓN -------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PW = os.environ.get("GMAIL_APP_PW")

CANAL_TELEGRAM = "forocoches_oficial"
REMITENTE_NEWSLETTER = "newsletter.forocoches.com"  # dominio del remitente a vigilar

PALABRAS_CLAVE = [
    "invitación", "invitaciones", "invitacion",
    "código", "codigo", "canjear", "invi ", "invis"
]

URL_CANJE = "https://forocoches.com/codigo/"

# Patrón principal, basado en el formato REAL que usa ForoCoches en su Telegram:
#   "Invitaciones: rbhQgcf7qy kx5mpUjKc a canjear en forocoches.com/codigo"
# Es decir: la palabra "Invitaciones:" (o "Invitación:"), seguida de uno o
# varios códigos alfanuméricos separados por espacios, hasta que aparece
# "a canjear" o termina la frase.
PATRON_BLOQUE_CODIGOS = re.compile(
    r'invitaci[oó]n(?:es)?\s*:\s*([A-Za-z0-9]{6,20}(?:\s+[A-Za-z0-9]{6,20})*)',
    re.IGNORECASE,
)

# Patrones alternativos, por si alguna vez cambian el formato a "código: X"
PATRONES_CODIGO_ALT = [
    r'(?:c[oó]digo|code)\s*[:\-]?\s*["\']?([A-Za-z0-9_\-]{4,20})["\']?',
    r'(?:canjea|canjear|usa|introduce)\s+(?:el\s+)?(?:c[oó]digo\s+)?["\']?([A-Za-z0-9_\-]{4,20})["\']?',
]

PALABRAS_A_IGNORAR = {"de", "para", "con", "una", "https", "http", "en", "el", "la"}


def extraer_codigos(texto):
    """Intenta extraer TODOS los códigos de invitación del texto del mensaje.
    Devuelve una lista de códigos encontrados (puede estar vacía si no
    detecta ninguno con certeza, ej: es un acertijo o viene en una imagen)."""

    # 1. Intento principal: formato real "Invitaciones: cod1 cod2 ..."
    m = PATRON_BLOQUE_CODIGOS.search(texto)
    if m:
        bloque = m.group(1)
        candidatos = bloque.split()
        codigos = [c for c in candidatos if c.lower() not in PALABRAS_A_IGNORAR]
        if codigos:
            return codigos

    # 2. Alternativas por si el formato cambia
    for patron in PATRONES_CODIGO_ALT:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            candidato = m.group(1)
            if candidato.lower() not in PALABRAS_A_IGNORAR:
                return [candidato]

    return []


ARCHIVO_ESTADO = "ultimo_visto.json"
# -------------------------------------------------------


def cargar_estado():
    if os.path.exists(ARCHIVO_ESTADO):
        with open(ARCHIVO_ESTADO, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"telegram_ids": [], "email_ids": []}


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def enviar_telegram(mensaje, botones=None):
    """Envía un mensaje por Telegram. Si se pasa 'botones' (una lista de
    filas, cada fila una lista de dicts de botón), se añade un teclado en
    línea al mensaje."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": mensaje}
    if botones:
        data["reply_markup"] = json.dumps({"inline_keyboard": botones})
    r = requests.post(url, data=data, timeout=15)
    if not r.ok:
        print(f"[!] Error enviando notificación: {r.text}", file=sys.stderr)


def construir_botones_codigos(codigos):
    """Construye un teclado en línea: un botón 'Copiar' por cada código
    detectado, y una última fila con el botón para abrir la web de canje."""
    filas = []
    for codigo in codigos:
        filas.append([{
            "text": f"📋 Copiar {codigo}",
            "copy_text": {"text": codigo},
        }])
    filas.append([{
        "text": "🔗 Ir a canjear",
        "url": URL_CANJE,
    }])
    return filas


def contiene_palabra_clave(texto):
    texto_l = texto.lower()
    return any(p.lower() in texto_l for p in PALABRAS_CLAVE)


# ---------------------- PARTE 1: TELEGRAM ----------------------

def revisar_telegram(estado):
    ids_vistos = set(estado.get("telegram_ids", []))
    nuevos = 0

    url = f"https://t.me/s/{CANAL_TELEGRAM}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FCWatcher/1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[!] Error al descargar canal de Telegram: {e}", file=sys.stderr)
        return 0

    bloques = re.findall(
        r'data-post="[^/]+/(\d+)".*?<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
        resp.text,
        re.DOTALL,
    )

    for msg_id, texto_html in bloques:
        if msg_id in ids_vistos:
            continue
        texto = re.sub(r"<[^>]+>", " ", texto_html)
        texto = unescape(texto)
        texto = re.sub(r"\s+", " ", texto).strip()

        if contiene_palabra_clave(texto):
            codigos = extraer_codigos(texto)
            if codigos:
                lista_codigos = "\n".join(f"• {c}" for c in codigos)
                aviso = (f"🚗 [TELEGRAM] ¡Código(s) detectado(s)!\n\n"
                          f"{lista_codigos}\n\n"
                          f"Mensaje original: {texto}\n"
                          f"https://t.me/{CANAL_TELEGRAM}/{msg_id}")
                botones = construir_botones_codigos(codigos)
                enviar_telegram(aviso, botones=botones)
            else:
                aviso = (f"🚗 [TELEGRAM] Posible invitación (código no detectado automáticamente, revisa el mensaje):\n\n"
                          f"{texto}\n"
                          f"https://t.me/{CANAL_TELEGRAM}/{msg_id}")
                botones = [[{"text": "🔗 Ir a canjear", "url": URL_CANJE}]]
                enviar_telegram(aviso, botones=botones)
            print(aviso)
            nuevos += 1
        ids_vistos.add(msg_id)

    estado["telegram_ids"] = list(ids_vistos)[-500:]
    return nuevos


# ---------------------- PARTE 2: GMAIL ----------------------

def decodificar_asunto(asunto_raw):
    partes = decode_header(asunto_raw)
    resultado = ""
    for texto, codificacion in partes:
        if isinstance(texto, bytes):
            resultado += texto.decode(codificacion or "utf-8", errors="ignore")
        else:
            resultado += texto
    return resultado


def extraer_texto_plano(msg):
    """Extrae el texto del cuerpo del email, ya sea texto plano o HTML."""
    cuerpo = ""
    if msg.is_multipart():
        for parte in msg.walk():
            content_type = parte.get_content_type()
            if content_type in ("text/plain", "text/html"):
                try:
                    payload = parte.get_payload(decode=True)
                    if payload:
                        cuerpo += payload.decode(errors="ignore")
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                cuerpo = payload.decode(errors="ignore")
        except Exception:
            pass

    # Si venía en HTML, quitamos etiquetas para dejar texto legible
    cuerpo = re.sub(r"<[^>]+>", " ", cuerpo)
    cuerpo = unescape(cuerpo)
    cuerpo = re.sub(r"\s+", " ", cuerpo).strip()
    return cuerpo


def revisar_gmail(estado):
    if not GMAIL_USER or not GMAIL_APP_PW:
        print("[info] GMAIL_USER/GMAIL_APP_PW no configurados, saltando revisión de correo.")
        return 0

    ids_vistos = set(estado.get("email_ids", []))
    nuevos = 0

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(GMAIL_USER, GMAIL_APP_PW)
        imap.select("INBOX")

        # Busca correos del remitente de la newsletter, de los últimos 2 días
        criterio = f'(FROM "{REMITENTE_NEWSLETTER}")'
        _, datos = imap.search(None, criterio)
        ids_correos = datos[0].split()

        # Solo revisamos los últimos 10 para no sobrecargar
        for eid in ids_correos[-10:]:
            eid_str = eid.decode()
            if eid_str in ids_vistos:
                continue

            _, msg_data = imap.fetch(eid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            asunto = decodificar_asunto(msg.get("Subject", ""))
            cuerpo = extraer_texto_plano(msg)

            texto_completo = f"{asunto} {cuerpo}"
            if contiene_palabra_clave(texto_completo):
                codigos = extraer_codigos(texto_completo)
                extracto = cuerpo[:500] + ("..." if len(cuerpo) > 500 else "")
                if codigos:
                    lista_codigos = "\n".join(f"• {c}" for c in codigos)
                    aviso = (f"📧 [NEWSLETTER] ¡Código(s) detectado(s)!\n\n"
                              f"{lista_codigos}\n\n"
                              f"Asunto: {asunto}\n\n{extracto}")
                    botones = construir_botones_codigos(codigos)
                    enviar_telegram(aviso, botones=botones)
                else:
                    aviso = (f"📧 [NEWSLETTER] Correo nuevo con posible invitación "
                              f"(código no detectado automáticamente, revisa el correo):\n\n"
                              f"Asunto: {asunto}\n\n{extracto}")
                    botones = [[{"text": "🔗 Ir a canjear", "url": URL_CANJE}]]
                    enviar_telegram(aviso, botones=botones)
                print(aviso)
                nuevos += 1

            ids_vistos.add(eid_str)

        imap.logout()
    except Exception as e:
        print(f"[!] Error al revisar Gmail: {e}", file=sys.stderr)
        return 0

    estado["email_ids"] = list(ids_vistos)[-200:]
    return nuevos


# ---------------------- MAIN ----------------------

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("[!] Faltan BOT_TOKEN o CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    estado = cargar_estado()

    nuevos_tg = revisar_telegram(estado)
    nuevos_email = revisar_gmail(estado)

    guardar_estado(estado)

    print(f"[info] Revisión completada ({time.strftime('%Y-%m-%d %H:%M:%S')}). "
          f"Telegram: {nuevos_tg} nuevos | Email: {nuevos_email} nuevos")


if __name__ == "__main__":
    main()
