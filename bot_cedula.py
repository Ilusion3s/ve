import logging
import os
import re
import asyncio
import io
import random
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup
import urllib3
from PIL import Image, ImageDraw, ImageFont

import psycopg
from contextlib import contextmanager

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, filters, ConversationHandler, CallbackQueryHandler
)
from telegram.request import HTTPXRequest

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# 🔴 CONFIGURACIÓN (ESCRITA DIRECTAMENTE AQUÍ)
# ============================================================
TOKEN = '9065471216:AAE1ivIrooUq7da85LsDKTKtwnb7EbfMcqQ'
URL_EDITOR_FIRMA = 'https://strong-capybara-5e9b28.netlify.app'

ADMIN_ID = 5460840050  # <-- Tu ID de Telegram

# 🔴 CADENA DE CONEXIÓN A SUPABASE (POOLER)
DATABASE_URL = "postgresql://postgres.plebccjzhoedwymxvprp:T6tdFyEvqpkT2NHZ@aws-1-us-west-2.pooler.supabase.com:5432/postgres"

CARPETA = os.path.dirname(os.path.abspath(__file__))
ARCHIVO_PLANTILLA = os.path.join(CARPETA, 'plantilla.png')
ARCHIVO_FOTO = os.path.join(CARPETA, 'foto.jpg')
ARCHIVO_FIRMA = os.path.join(CARPETA, 'firma.png')
ARCHIVO_SALIDA = os.path.join(CARPETA, 'cedula_final.png')

# --- ESTADOS ---
MENU, CEDULA, APELLIDOS, NOMBRES, FECHA_NAC, ESTADO_CIVIL, FOTO, FIRMA = range(8)
SEN_TIPO, SEN_DOC, SEN_USUARIO, SEN_CAPTCHA = range(20, 24)

# --- COORDENADAS CÉDULA ---
COORDENADAS = {
    'cedula':       {'x': 407, 'y': 182, 'tamano': 13},
    'apellidos':    {'x': 158, 'y': 221, 'tamano': 12},
    'nombres':      {'x': 157, 'y': 267, 'tamano': 12},
    'fecha_nac':    {'x': 278, 'y': 416, 'tamano': 12},
    'estado_civil': {'x': 448, 'y': 416, 'tamano': 12},
}
COORD_FOTO = {'x': 619, 'y': 302, 'ancho': 317}
COORD_FIRMA = {'x': 76, 'y': 320, 'ancho': 125}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ============================================================
# SISTEMA DE TOKENS (CON MANEJO DE ERRORES)
# ============================================================
@contextmanager
def db():
    try:
        con = psycopg.connect(DATABASE_URL, connect_timeout=30, sslmode="require")
        con.autocommit = False
        yield con
        con.commit()
    except Exception as e:
        logger.error(f"❌ ERROR EN BASE DE DATOS: {e}")
        raise e
    finally:
        try:
            con.close()
        except: pass

def init_db():
    try:
        with db() as con:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usuarios (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    nombre TEXT,
                    tokens INTEGER NOT NULL DEFAULT 0,
                    registrado TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    actualizado TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
        logger.info("🗄️ DB Postgres/Supabase lista y conectada.")
    except Exception as e:
        logger.error(f"⚠️ Error de conexión con Supabase: {e}")
        logger.error("El bot arrancará, pero los tokens fallarán hasta que la conexión se restablezca.")

def get_tokens(user_id):
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT tokens FROM usuarios WHERE user_id=%s", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0

def registrar_usuario(user_id, username, nombre):
    with db() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO usuarios(user_id, username, nombre) VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                username = COALESCE(EXCLUDED.username, usuarios.username),
                nombre = COALESCE(EXCLUDED.nombre, usuarios.nombre),
                actualizado = NOW()
        """, (user_id, username, nombre))

def add_tokens(user_id, cantidad, admin_id):
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT tokens FROM usuarios WHERE user_id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row: return 0
        actual = int(row[0])
        nuevo = max(0, actual + cantidad)
        cur.execute("UPDATE usuarios SET tokens=%s, actualizado=NOW() WHERE user_id=%s", (nuevo, user_id))
        return nuevo

def consumir_token(user_id):
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT tokens FROM usuarios WHERE user_id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row or int(row[0]) <= 0: return False
        cur.execute("UPDATE usuarios SET tokens=tokens-1, actualizado=NOW() WHERE user_id=%s", (user_id,))
        return True

def buscar_por_username(username):
    if not username: return None
    username = username.lstrip("@").lower()
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT user_id FROM usuarios WHERE LOWER(username)=%s", (username,))
        row = cur.fetchone()
        return int(row[0]) if row else None

def es_admin(user_id):
    return user_id == ADMIN_ID

# ============================================================
# LÓGICA ORIGINAL DE CÉDULA
# ============================================================
def cargar_fuente(tamano, negrita=False):
    if negrita:
        rutas = [os.path.join(CARPETA, "LiberationSans-Bold.ttf"), "/system/fonts/Roboto-Bold.ttf"]
    else:
        rutas = [os.path.join(CARPETA, "LiberationSans-Regular.ttf"), "/system/fonts/Roboto-Regular.ttf"]
    for ruta in rutas:
        if os.path.exists(ruta):
            try: return ImageFont.truetype(ruta, tamano)
            except: pass
    return ImageFont.load_default()

def generar_cedula(datos):
    img = Image.open(ARCHIVO_PLANTILLA).convert("RGB")
    draw = ImageDraw.Draw(img)
    ancho_real, alto_real = img.size
    factor_escala = ancho_real / 400.0

    if os.path.exists(ARCHIVO_FOTO):
        foto = Image.open(ARCHIVO_FOTO)
        ancho_foto = COORD_FOTO['ancho']
        alto_foto = int(foto.height * (ancho_foto / foto.width))
        foto = foto.resize((ancho_foto, alto_foto))
        img.paste(foto, (COORD_FOTO['x'], COORD_FOTO['y']))

    if os.path.exists(ARCHIVO_FIRMA):
        firma_img = Image.open(ARCHIVO_FIRMA).convert("RGBA")
        ancho_firma = COORD_FIRMA['ancho']
        alto_firma = int(firma_img.height * (ancho_firma / firma_img.width))
        firma_img = firma_img.resize((ancho_firma, alto_firma))
        img.paste(firma_img, (COORD_FIRMA['x'], COORD_FIRMA['y']), firma_img)

    campos_negrita = ['cedula', 'fecha_nac', 'estado_civil']
    for campo, coord in COORDENADAS.items():
        texto = datos[campo]
        tamano_real = int(coord['tamano'] * factor_escala)
        fuente = cargar_fuente(tamano_real, negrita=campo in campos_negrita)
        draw.text((coord['x'], coord['y']), texto, fill=(0, 0, 0), font=fuente)

    img.save(ARCHIVO_SALIDA)
    return ARCHIVO_SALIDA

def parsear_comando_datos(texto):
    texto = re.sub(r'^/datos\s+', '', texto, flags=re.IGNORECASE).strip()
    datos = {'cedula': None, 'apellidos': None, 'nombres': None, 'fecha_nac': None, 'estado_civil': None}
    estados_civiles = ['SOLTERO', 'SOLTERA', 'CASADO', 'CASADA', 'VIUDO', 'VIUDA', 'DIVORCIADO', 'DIVORCIADA', 'CONCUBINATO', 'CONCUBINA', 'SEPARADO', 'SEPARADA']
    palabras = texto.split()
    i = 0
    while i < len(palabras):
        palabra_actual = palabras[i].lower()
        palabra_upper = palabras[i].upper()
        
        if palabra_actual in ['cedula', 'cédula', 'ci']:
            i += 1
            if i < len(palabras): datos['cedula'] = palabras[i]; i += 1; continue
        elif palabra_actual in ['apellido', 'apellidos']:
            i += 1; apellidos_lista = []
            while i < len(palabras):
                p_lower = palabras[i].lower(); p_upper = palabras[i].upper()
                if (p_lower in ['nombre', 'nombres', 'cedula', 'cédula', 'ci'] or '/' in palabras[i] or p_upper in estados_civiles): break
                apellidos_lista.append(palabras[i].upper()); i += 1
            datos['apellidos'] = ' '.join(apellidos_lista); continue
        elif palabra_actual in ['nombre', 'nombres']:
            i += 1; nombres_lista = []
            while i < len(palabras):
                p_lower = palabras[i].lower(); p_upper = palabras[i].upper()
                if (p_lower in ['apellido', 'apellidos', 'cedula', 'cédula', 'ci'] or '/' in palabras[i] or p_upper in estados_civiles): break
                nombres_lista.append(palabras[i].upper()); i += 1
            datos['nombres'] = ' '.join(nombres_lista); continue
        elif '/' in palabras[i]:
            match = re.match(r'^(\d{1,2}/\d{1,2}/\d{2,4})$', palabras[i])
            if match: datos['fecha_nac'] = palabras[i]
            i += 1; continue
        elif palabra_upper in estados_civiles:
            datos['estado_civil'] = palabra_upper; i += 1; continue
        else: i += 1
    
    faltantes = [k for k, v in datos.items() if not v]
    if faltantes: return None, faltantes
    return datos, None

# ============================================================
# LÓGICA SENIAT
# ============================================================
BASE_URL = "https://contribuyente.seniat.gob.ve"
URL_INICIO = f"{BASE_URL}/recusuarioclave/inicio.do"
URL_BUSCAR = f"{BASE_URL}/recusuarioclave/buscarContribuyente.do"
URL_VALIDAR = f"{BASE_URL}/recusuarioclave/validarRespuestasSeguridad.do"
URL_REGISTRAR = f"{BASE_URL}/recusuarioclave/registrarClave.do"
URL_CAPTCHA = f"{BASE_URL}/recusuarioclave/CaptchaCU.jpg"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:115.0) Gecko/20100101 Firefox/115.0"

PALABRAS_CLAVE = ["seniat","manzana","cebolla","albaca","puerta","silla","mesa","casa","perro","gato","carro","libro","agua","tierra","fuego","viento","sol","luna","estrella","mar","rio","monte","flor","arbol","hoja","piedra","oro","plata","cobre","hierro","madera","vidrio","papel","tela","zapato","camisa","pantalon","sombrero","reloj","llave","puente","camino","ciudad","pueblo","campo","playa","isla","barco","avion","tren","bicicleta","computadora","telefono","radio","television","ventana","cocina","baño","cuarto","sala","techo","piso","pared","luz","sombra","color","rojo","azul","verde","amarillo","negro","blanco","gris","morado","naranja","rosa"]
PALABRAS_RESPUESTA = ["segueta","olbert","puerta","ubicacion","emilia","albaca","cebolla","sillon","casamarilla","manzana","perro","gato","carro","libro","agua","tierra","fuego","viento","sol","luna","estrella","mar","rio","monte","flor","arbol","hoja","piedra","oro","plata","cobre","hierro","madera","vidrio","papel","tela","zapato","camisa","pantalon","sombrero","reloj","llave","puente","camino","ciudad","pueblo","campo","playa","isla","barco","avion","tren","bicicleta","computadora","telefono","radio","television","ventana","cocina","baño","cuarto","sala","techo","piso","pared","luz","sombra","color","rojo","azul","verde","amarillo","negro","blanco","gris","morado","naranja","rosa"]
PREGUNTAS_SEGURIDAD = ["Color favorito","Nombre de tu mascota","Ciudad de nacimiento","Comida favorita","Nombre de tu madre","Nombre de tu padre","Apellido de soltera de tu madre","Nombre de tu primera escuela","Nombre de tu mejor amigo de infancia","Modelo de tu primer carro"]

def generar_clave():
    palabra = random.choice(PALABRAS_CLAVE)
    if len(palabra) > 6: palabra = palabra[:6]
    num = random.randint(10, 9999)
    clave = f"{palabra}{num}"
    if len(clave) > 10:
        exceso = len(clave) - 10
        clave = f"{palabra}{str(num)[:-exceso]}" if exceso < len(str(num)) else palabra[:8]
    elif len(clave) < 8:
        faltan = 8 - len(clave)
        clave = f"{palabra}{num}{random.randint(0, 9) * faltan}"
    if not clave[0].islower() or not any(c.isdigit() for c in clave):
        clave = f"seniat{random.randint(10, 999)}"
    clave = re.sub(r"[^a-z0-9]", "", clave.lower())
    if len(clave) > 10: clave = clave[:10]
    if len(clave) < 8:  clave = clave + "1" * (8 - len(clave))
    return clave

def generar_pregunta_respuesta():
    return random.choice(PREGUNTAS_SEGURIDAD), random.choice(PALABRAS_RESPUESTA)

def nueva_sesion():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Origin": BASE_URL, "X-Requested-With": "XMLHttpRequest"})
    return s

def request_con_reintentos(sesion, method, url, max_int=3, **kwargs):
    for intento in range(1, max_int + 1):
        try:
            r = sesion.request(method, url, timeout=30, verify=False, **kwargs)
            if r.status_code < 500: return r
        except Exception:
            if intento == max_int: return None
            time.sleep(4)
    return None

def detectar_error_seniat(html):
    low = html.lower()
    if "captcha" in low and ("incorrect" in low or "inválid" in low): return "captcha_malo", "🔐 CAPTCHA incorrecto."
    if "clave" in low and ("ya existe" in low or "duplicad" in low): return "clave_dup", "⚠️ Clave ya en uso."
    if "no se encontr" in low and "contribuyente" in low: return "no_existe", "❌ Contribuyente no encontrado."
    if "error" in low: return "error", "❌ Error en el SENIAT."
    return None, None

def barra(paso, total=4, ancho=10):
    llenos = int(paso * ancho / total)
    return f"[{'▓' * llenos}{'░' * (ancho - llenos)}] Paso {paso}/{total}"

PIE_CANCELAR = "\n\n_/cancelar para abortar_"

# ============================================================
# MENÚ Y COMANDOS
# ============================================================
def teclado_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🪪 Crear Cédula", callback_data="menu:cedula")],
        [InlineKeyboardButton("🏛️ Recuperar RIF (SENIAT)", callback_data="menu:seniat")],
        [InlineKeyboardButton("💰 Mis tokens", callback_data="menu:saldo")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for f in [ARCHIVO_FOTO, ARCHIVO_FIRMA]: 
        if os.path.exists(f): os.remove(f)
    context.user_data.clear()
    
    u = update.effective_user
    registrar_usuario(u.id, u.username, u.full_name)
    
    tokens = get_tokens(u.id)
    msg = f"👋 ¡Hola {u.first_name}!\n\n"
    if es_admin(u.id):
        msg += "👑 *Administrador* (Acceso ilimitado)\n\n"
    else:
        msg += f"💰 Tokens disponibles: *{tokens}*\n\n"
    
    msg += "Elige qué quieres hacer:"
    await update.message.reply_text(msg, parse_mode='Markdown', reply_markup=teclado_menu())
    return MENU

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    opcion = q.data.split(":")[1]
    uid = update.effective_user.id

    if opcion in ("cedula", "seniat"):
        if not es_admin(uid) and get_tokens(uid) <= 0:
            await q.edit_message_text("🚫 *Sin tokens.*\n\nContacta al admin.\n/token para consultar.", parse_mode='Markdown')
            return MENU

    if opcion == "saldo":
        if es_admin(uid):
            await q.edit_message_text("👑 *Admin.*\n\n`/token` · `/quitar` · `/reset` · `/resetall`", parse_mode='Markdown')
        else:
            await q.edit_message_text(f"💰 *Tu saldo:* *{get_tokens(uid)}* token(s).", parse_mode='Markdown')
        return MENU

    if opcion == "cedula":
        await q.edit_message_text(
            "🪪 *Generador de Cédula*\n\n"
            "📋 *OPCIÓN 1: Rápida con /datos*\n"
            "`/datos cedula 17.456.678 Apellido HERNÁNDEZ COVA Nombre JOSE ANGEL 23/07/1990 SOLTERO`\n\n"
            "📝 *OPCIÓN 2: Paso a paso*\n"
            "Envía el *número de cédula* (solo números):" + PIE_CANCELAR,
            parse_mode='Markdown'
        )
        return CEDULA

    context.user_data["sesion"] = nueva_sesion()
    context.user_data["inicio"] = time.time()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🪪 Cédula", callback_data="tipo:1")], [InlineKeyboardButton("📄 RIF", callback_data="tipo:2")]])
    await q.edit_message_text(f"🏛️ *Recuperar Clave SENIAT*\n\n{barra(1)}\n\n¿Qué documento usas?" + PIE_CANCELAR, parse_mode='Markdown', reply_markup=kb)
    return SEN_TIPO

# --- HANDLERS CÉDULA ---
async def comando_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not es_admin(uid) and get_tokens(uid) <= 0:
        await update.message.reply_text("🚫 *Sin tokens.* /token para consultar.", parse_mode='Markdown')
        return ConversationHandler.END

    for f in [ARCHIVO_FOTO, ARCHIVO_FIRMA]: 
        if os.path.exists(f): os.remove(f)
    context.user_data.clear()
    
    datos, faltantes = parsear_comando_datos(update.message.text)
    if datos is None:
        faltantes_str = ', '.join([f"`{f}`" for f in faltantes])
        await update.message.reply_text(f"❌ *Faltan datos:* {faltantes_str}\n\nUsa el formato:\n`/datos cedula 17.456.678 Apellido HERNÁNDEZ COVA Nombre JOSE ANGEL 23/07/1990 SOLTERO`", parse_mode='Markdown')
        return ConversationHandler.END
    
    context.user_data.update(datos)
    await update.message.reply_text(f"✅ *Datos recibidos:*\n\n🆔 Cédula: `{datos['cedula']}`\n👤 Apellidos: `{datos['apellidos']}`\n📛 Nombres: `{datos['nombres']}`\n📅 Fecha Nac: `{datos['fecha_nac']}`\n💍 Estado Civil: `{datos['estado_civil']}`\n\n📸 Ahora envía la *FOTO* del titular:", parse_mode='Markdown')
    return ConversationHandler.END

async def cedula(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cedula'] = update.message.text
    await update.message.reply_text("Envía los *APELLIDOS*:", parse_mode='Markdown')
    return APELLIDOS

async def apellidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['apellidos'] = update.message.text.upper()
    await update.message.reply_text("Envía los *NOMBRES*:", parse_mode='Markdown')
    return NOMBRES

async def nombres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nombres'] = update.message.text.upper()
    await update.message.reply_text("Envía la *FECHA DE NACIMIENTO* (DD/MM/AAAA):", parse_mode='Markdown')
    return FECHA_NAC

async def fecha_nac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['fecha_nac'] = update.message.text
    await update.message.reply_text("Envía el *ESTADO CIVIL*:", parse_mode='Markdown')
    return ESTADO_CIVIL

async def estado_civil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['estado_civil'] = update.message.text.upper()
    await update.message.reply_text("Envía la *FOTO* del titular (como imagen):", parse_mode='Markdown')
    return ConversationHandler.END

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('cedula') or not context.user_data.get('nombres'):
        return

    try:
        foto = update.message.photo[-1]
        archivo = await foto.get_file()
        await archivo.download_to_drive(ARCHIVO_FOTO)
    except Exception as e:
        await update.message.reply_text(f"❌ Error al recibir la foto: {e}")
        return
    
    boton = InlineKeyboardButton("🖊️ Firmar aquí", web_app=WebAppInfo(url=URL_EDITOR_FIRMA))
    await update.message.reply_text("✅ Foto recibida.\n\n*AHORA LA FIRMA*\n\nToca el botón, dibuja tu firma y envía.", parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[boton]]))

async def recibir_firma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('cedula') or not context.user_data.get('nombres'):
        return

    if not update.message.document:
        await update.message.reply_text("Esperando la firma... Toca el botón de arriba.")
        return
    
    try:
        doc = update.message.document
        archivo = await doc.get_file()
        await archivo.download_to_drive(ARCHIVO_FIRMA)
        
        await update.message.reply_text("✅ Firma recibida. Generando cédula... ⏳")
        ruta = generar_cedula(context.user_data)
        
        uid = update.effective_user.id
        if not es_admin(uid):
            consumir_token(uid)
            saldo = get_tokens(uid)
        else:
            saldo = "∞ (admin)"

        with open(ruta, 'rb') as f:
            await update.message.reply_photo(photo=f, caption=f"✅ ¡Aquí tienes tu cédula!\n💰 Tokens restantes: *{saldo}*", parse_mode='Markdown')
        
        await update.message.reply_text("Escribe /start para volver al menú.")
        context.user_data.clear()
    except Exception as e:
        await update.message.reply_text(f"❌ Error al generar: {e}")

# --- HANDLERS SENIAT ---
async def sen_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    tipo = q.data.split(":")[1]
    context.user_data["tipo"] = tipo
    txt = "cédula" if tipo == "1" else "RIF (V123456789)"
    await q.edit_message_text(f"{barra(2)}\n\nEscribe tu {txt}:" + PIE_CANCELAR, parse_mode='Markdown')
    return SEN_DOC

async def sen_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.text.strip().upper().replace("-", "").replace(".", "").replace(" ", "")
    tipo = context.user_data["tipo"]
    if tipo == "1" and (not doc.isdigit() or len(doc) < 6):
        await update.message.reply_text("❌ Cédula inválida." + PIE_CANCELAR, parse_mode='Markdown')
        return SEN_DOC
    if tipo == "2" and not re.match(r"^[VEJPG]\d{9}$", doc):
        await update.message.reply_text("❌ RIF inválido." + PIE_CANCELAR, parse_mode='Markdown')
        return SEN_DOC

    context.user_data["documento"] = doc
    msg = await update.message.reply_text(f"{barra(2)}\n🔎 Buscando..." + PIE_CANCELAR, parse_mode='Markdown')
    s = context.user_data["sesion"]
    
    try:
        r1 = request_con_reintentos(s, "POST", URL_BUSCAR, data={"tipoDocumento": tipo, "documento": doc}, headers={"Referer": URL_INICIO})
        if not r1: raise Exception("No responde")
        
        soup = BeautifulSoup(r1.text, "html5lib")
        hid = soup.find("input", {"name": "idContribuyente"})
        if not hid:
            await msg.edit_text("❌ Contribuyente no encontrado." + PIE_CANCELAR, parse_mode='Markdown')
            return ConversationHandler.END
        
        id_c = hid["value"]
        await msg.edit_text(f"{barra(3)}\n🔓 Bypass..." + PIE_CANCELAR, parse_mode='Markdown')
        
        r2 = request_con_reintentos(s, "POST", URL_VALIDAR, data={"idContribuyente": id_c, "tipoContribuyente": tipo}, headers={"Referer": URL_INICIO})
        soup2 = BeautifulSoup(r2.text, "html5lib")
        
        usuarios = []
        for a in soup2.find_all("a", id=re.compile(r"lnkUsuario")):
            u = a.get_text(strip=True)
            tr = a.find_parent("tr")
            tds = tr.find_all("td") if tr else []
            usuarios.append({"usuario": u, "nombre": tds[1].get_text(strip=True) if len(tds) > 1 else "?"})
        
        if not usuarios:
            await msg.edit_text("❌ No se encontraron usuarios." + PIE_CANCELAR, parse_mode='Markdown')
            return ConversationHandler.END

        context.user_data["usuarios"] = usuarios
        botones = [[InlineKeyboardButton(f"👤 {u['usuario']} - {u['nombre'][:30]}", callback_data=f"user:{u['usuario']}")] for u in usuarios]
        await msg.edit_text(f"✅ *Bypass OK*\n\n{barra(3)}\n\nElige:" + PIE_CANCELAR, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(botones))
        return SEN_USUARIO

    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}" + PIE_CANCELAR, parse_mode='Markdown')
        return ConversationHandler.END

async def sen_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["usuario"] = q.data.split(":", 1)[1]
    clave = generar_clave()
    pregunta, respuesta = generar_pregunta_respuesta()
    context.user_data.update({"clave": clave, "pregunta": pregunta, "respuesta": respuesta})
    
    await q.edit_message_text(f"👤 `{context.user_data['usuario']}`\n\n{barra(4)}\n\n🔑 `{clave}`\n❓ {pregunta}\n💬 `{respuesta}`\n\n🔐 Descargando CAPTCHA..." + PIE_CANCELAR, parse_mode='Markdown')
    
    s = context.user_data["sesion"]
    r_cap = request_con_reintentos(s, "GET", URL_CAPTCHA, headers={"Referer": URL_INICIO, "Accept": "image/*"})
    if not r_cap or r_cap.status_code != 200:
        await q.edit_message_text("⚠️ No se pudo descargar CAPTCHA." + PIE_CANCELAR, parse_mode='Markdown')
        return ConversationHandler.END

    await update.effective_chat.send_photo(photo=io.BytesIO(r_cap.content), caption=f"🔐 *CAPTCHA*\n\nEscribe el código. 3 intentos." + PIE_CANCELAR, parse_mode='Markdown')
    return SEN_CAPTCHA

async def sen_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    captcha = update.message.text.strip()
    intentos = context.user_data.get("intentos_captcha", 0) + 1
    context.user_data["intentos_captcha"] = intentos
    
    s = context.user_data["sesion"]
    msg = await update.message.reply_text(f"🔄 Registrando ({intentos}/3)...")
    
    try:
        r = request_con_reintentos(s, "POST", URL_REGISTRAR, data={
            "usuario": context.user_data["usuario"], "clave": context.user_data["clave"],
            "confirmarClave": context.user_data["clave"], "pregunta": context.user_data["pregunta"],
            "respuesta": context.user_data["respuesta"], "codigoCaptcha": captcha
        }, headers={"Referer": URL_INICIO})
        
        if not r: raise Exception("No responde")
        
        tipo_err, txt = detectar_error_seniat(r.text)
        if tipo_err == "captcha_malo" and intentos < 3:
            await msg.edit_text(f"🔐 CAPTCHA incorrecto ({intentos}/3). Otro:", parse_mode='Markdown')
            r_cap = request_con_reintentos(s, "GET", URL_CAPTCHA, headers={"Referer": URL_INICIO})
            if r_cap and r_cap.status_code == 200:
                await update.message.reply_photo(photo=io.BytesIO(r_cap.content), caption=f"🔐 Nuevo CAPTCHA ({intentos + 1}/3):")
            return SEN_CAPTCHA
            
        if tipo_err:
            await msg.edit_text(txt + PIE_CANCELAR, parse_mode='Markdown')
            return ConversationHandler.END

        uid = update.effective_user.id
        if not es_admin(uid):
            consumir_token(uid)
            saldo = get_tokens(uid)
        else:
            saldo = "∞ (admin)"

        await msg.edit_text(f"✅ *¡CLAVE REGISTRADA!*\n\n👤 `{context.user_data['usuario']}`\n🔑 `{context.user_data['clave']}`\n\n💰 Tokens: *{saldo}*", parse_mode='Markdown')
        context.user_data.clear()
        await update.message.reply_text("Elige otra opción:", reply_markup=teclado_menu())
        return MENU
        
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}" + PIE_CANCELAR, parse_mode='Markdown')
        return ConversationHandler.END

# --- COMANDOS DE TOKENS ---
def _resolver_target(update, ctx, arg):
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        u = update.message.reply_to_message.from_user
        return u.id, u.username, u.full_name
    if arg and arg.startswith("@"):
        tid = buscar_por_username(arg)
        return (tid, arg, None) if tid else (None, None, None)
    if arg and arg.isdigit():
        return int(arg), None, None
    return None, None, None

async def comando_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not es_admin(uid):
        await update.message.reply_text(f"💰 *Tu saldo:* *{get_tokens(uid)}* token(s).", parse_mode='Markdown')
        return

    args = context.args or []
    if not args:
        with db() as con:
            cur = con.cursor()
            cur.execute("SELECT user_id, username, tokens FROM usuarios ORDER BY tokens DESC LIMIT 50")
            db_usuarios = cur.fetchall()
        
        if not db_usuarios:
            await update.message.reply_text("No hay usuarios.")
            return
        
        lineas = ["📋 *Usuarios:*\n"]
        for u_id, u_name, t in db_usuarios:
            lineas.append(f"• `{u_id}` @{u_name or '?'} → *{t}*")
        await update.message.reply_text("\n".join(lineas), parse_mode='Markdown')
        return

    if len(args) < 2:
        await update.message.reply_text("❌ Uso: `/token 5 @user`", parse_mode='Markdown')
        return

    try: cantidad = int(args[0])
    except: return await update.message.reply_text("❌ Cantidad inválida.")
    
    tid, t_user, t_nom = _resolver_target(update, context, args[1])
    if not tid: return await update.message.reply_text("❌ Usuario no encontrado.")
    
    registrar_usuario(tid, t_user, t_nom)
    nuevo = add_tokens(tid, cantidad, uid)
    await update.message.reply_text(f"✅ *+{cantidad} tokens* a `{tid}`\n💰 Saldo: *{nuevo}*", parse_mode='Markdown')
    try: await context.bot.send_message(tid, f"🎁 *¡Recibiste tokens!*\n➕ *{cantidad}*\n💰 Saldo: *{nuevo}*", parse_mode='Markdown')
    except: pass

async def comando_quitar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    args = context.args or []
    if len(args) < 2: return await update.message.reply_text("❌ Uso: `/quitar 2 @user`", parse_mode='Markdown')
    try: cantidad = int(args[0])
    except: return await update.message.reply_text("❌ Cantidad inválida.")
    
    tid, t_user, t_nom = _resolver_target(update, context, args[1])
    if not tid: return await update.message.reply_text("❌ Usuario no encontrado.")
    
    nuevo = add_tokens(tid, -cantidad, update.effective_user.id)
    await update.message.reply_text(f"✅ *−{cantidad} tokens* a `{tid}`\n💰 Saldo: *{nuevo}*", parse_mode='Markdown')

async def comando_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    args = context.args or []
    if not args: return await update.message.reply_text("❌ Uso: `/reset @user`", parse_mode='Markdown')
    
    tid, _, _ = _resolver_target(update, context, args[0])
    if not tid: return await update.message.reply_text("❌ Usuario no encontrado.")
    
    with db() as con:
        cur = con.cursor()
        cur.execute("UPDATE usuarios SET tokens=0, actualizado=NOW() WHERE user_id=%s", (tid,))
    await update.message.reply_text(f"✅ Saldo de `{tid}` reseteado a *0*.", parse_mode='Markdown')

async def comando_resetall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not es_admin(update.effective_user.id): return
    with db() as con:
        cur = con.cursor()
        cur.execute("UPDATE usuarios SET tokens=0, actualizado=NOW()")
    await update.message.reply_text("✅ Todos los saldos reseteados a *0*.", parse_mode='Markdown')

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("👋 *Cancelado.*\n/start para volver.", parse_mode='Markdown')
    return ConversationHandler.END

# ============================================================
# MAIN
# ============================================================
def main():
    import time
    time.sleep(5)  # Espera 5 segundos para evitar bloqueos por reconexión rápida
    init_db()

    request = HTTPXRequest(connection_pool_size=8, connect_timeout=30.0, read_timeout=30.0, write_timeout=30.0, pool_timeout=30.0)
    application = ApplicationBuilder().token(TOKEN).request(request).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('datos', comando_datos),
        ],
        states={
            MENU: [CallbackQueryHandler(menu_callback, pattern=r"^menu:")],
            CEDULA: [MessageHandler(filters.TEXT & ~filters.COMMAND, cedula)],
            APELLIDOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, apellidos)],
            NOMBRES: [MessageHandler(filters.TEXT & ~filters.COMMAND, nombres)],
            FECHA_NAC: [MessageHandler(filters.TEXT & ~filters.COMMAND, fecha_nac)],
            ESTADO_CIVIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, estado_civil)],
            SEN_TIPO: [CallbackQueryHandler(sen_tipo, pattern=r"^tipo:")],
            SEN_DOC: [MessageHandler(filters.TEXT & ~filters.COMMAND, sen_doc)],
            SEN_USUARIO: [CallbackQueryHandler(sen_usuario, pattern=r"^user:")],
            SEN_CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, sen_captcha)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)],
        conversation_timeout=900
    )

    application.add_handler(conv)
    application.add_handler(CommandHandler('datos', comando_datos))
    application.add_handler(MessageHandler(filters.PHOTO, recibir_foto))
    application.add_handler(MessageHandler(filters.Document.ALL, recibir_firma))
    application.add_handler(CommandHandler("token", comando_token))
    application.add_handler(CommandHandler("quitar", comando_quitar))
    application.add_handler(CommandHandler("reset", comando_reset))
    application.add_handler(CommandHandler("resetall", comando_resetall))
    
    logger.info("🤖 Bot iniciado.")
    application.run_polling(poll_interval=1.0, timeout=30)

if __name__ == '__main__':
    main()