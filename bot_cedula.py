import logging
import os
import re
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, filters, ConversationHandler
)
from telegram.request import HTTPXRequest
from PIL import Image, ImageDraw, ImageFont

# ============================================================
# 🔴 CONFIGURACIÓN - CAMBIA ESTO
# ============================================================
import os
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'AQUI_TU_TOKEN_DE_BOTFATHER')
URL_EDITOR_FIRMA = 'https://strong-capybara-5e9b28.netlify.app'
# ============================================================

CARPETA = os.path.expanduser('~/cedula')
ARCHIVO_PLANTILLA = os.path.join(CARPETA, 'plantilla.png')
ARCHIVO_FOTO = os.path.join(CARPETA, 'foto.jpg')
ARCHIVO_FIRMA = os.path.join(CARPETA, 'firma.png')
ARCHIVO_SALIDA = os.path.join(CARPETA, 'cedula_final.png')

# --- ESTADOS ---
CEDULA, APELLIDOS, NOMBRES, FECHA_NAC, ESTADO_CIVIL, FOTO, FIRMA = range(7)

# --- COORDENADAS ---
COORDENADAS = {
    'cedula':       {'x': 407, 'y': 182, 'tamano': 13},
    'apellidos':    {'x': 158, 'y': 221, 'tamano': 12},
    'nombres':      {'x': 157, 'y': 267, 'tamano': 12},
    'fecha_nac':    {'x': 278, 'y': 416, 'tamano': 12},
    'estado_civil': {'x': 448, 'y': 416, 'tamano': 12},
}
COORD_FOTO = {'x': 619, 'y': 302, 'ancho': 317}
COORD_FIRMA = {'x': 76, 'y': 320, 'ancho': 125}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def cargar_fuente(tamano, negrita=False):
    if negrita:
        rutas = [os.path.join(CARPETA, "LiberationSans-Bold.ttf"), "/system/fonts/Roboto-Bold.ttf"]
    else:
        rutas = [os.path.join(CARPETA, "LiberationSans-Regular.ttf"), "/system/fonts/Roboto-Regular.ttf"]
    for ruta in rutas:
        if os.path.exists(ruta):
            try:
                return ImageFont.truetype(ruta, tamano)
            except:
                pass
    return ImageFont.load_default()


def generar_cedula(datos):
    logger.info(f"Generando cédula con datos: {datos}")
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
        logger.info("Foto insertada")

    if os.path.exists(ARCHIVO_FIRMA):
        firma_img = Image.open(ARCHIVO_FIRMA).convert("RGBA")
        ancho_firma = COORD_FIRMA['ancho']
        alto_firma = int(firma_img.height * (ancho_firma / firma_img.width))
        firma_img = firma_img.resize((ancho_firma, alto_firma))
        img.paste(firma_img, (COORD_FIRMA['x'], COORD_FIRMA['y']), firma_img)
        logger.info("Firma insertada")

    campos_negrita = ['cedula', 'fecha_nac', 'estado_civil']
    for campo, coord in COORDENADAS.items():
        texto = datos[campo]
        tamano_real = int(coord['tamano'] * factor_escala)
        es_negrita = campo in campos_negrita
        fuente = cargar_fuente(tamano_real, negrita=es_negrita)
        draw.text((coord['x'], coord['y']), texto, fill=(0, 0, 0), font=fuente)

    img.save(ARCHIVO_SALIDA)
    logger.info(f"Imagen guardada en: {ARCHIVO_SALIDA}")
    return ARCHIVO_SALIDA


async def enviar_con_reintentos(func, *args, **kwargs):
    for intento in range(3):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Intento {intento+1} falló: {e}")
            if intento < 2:
                await asyncio.sleep(2)
            else:
                raise


def parsear_comando_datos(texto):
    """
    Parsea el comando /datos con el formato:
    /datos cedula 17.456.678 Apellido HERNÁNDEZ COVA Nombre JOSE ANGEL 23/07/1990 SOLTERO
    
    Retorna (datos, faltantes):
    - datos: diccionario con los datos o None si hay error grave
    - faltantes: lista de campos que faltan
    """
    # Quitar /datos del inicio
    texto = re.sub(r'^/datos\s+', '', texto, flags=re.IGNORECASE).strip()
    
    datos = {
        'cedula': None,
        'apellidos': None,
        'nombres': None,
        'fecha_nac': None,
        'estado_civil': None,
    }
    
    # Estados civiles válidos
    estados_civiles = ['SOLTERO', 'SOLTERA', 'CASADO', 'CASADA', 
                       'VIUDO', 'VIUDA', 'DIVORCIADO', 'DIVORCIADA',
                       'CONCUBINATO', 'CONCUBINA', 'SEPARADO', 'SEPARADA']
    
    palabras = texto.split()
    
    i = 0
    while i < len(palabras):
        palabra_actual = palabras[i].lower()
        palabra_upper = palabras[i].upper()
        
        # Detectar "cedula", "cédula", "ci"
        if palabra_actual in ['cedula', 'cédula', 'ci']:
            i += 1
            if i < len(palabras):
                datos['cedula'] = palabras[i]
                i += 1
                continue
        
        # Detectar "apellido" o "apellidos"
        elif palabra_actual in ['apellido', 'apellidos']:
            i += 1
            apellidos_lista = []
            while i < len(palabras):
                p_lower = palabras[i].lower()
                p_upper = palabras[i].upper()
                if (p_lower in ['nombre', 'nombres', 'cedula', 'cédula', 'ci'] or
                    '/' in palabras[i] or
                    p_upper in estados_civiles):
                    break
                apellidos_lista.append(palabras[i].upper())
                i += 1
            datos['apellidos'] = ' '.join(apellidos_lista)
            continue
        
        # Detectar "nombre" o "nombres"
        elif palabra_actual in ['nombre', 'nombres']:
            i += 1
            nombres_lista = []
            while i < len(palabras):
                p_lower = palabras[i].lower()
                p_upper = palabras[i].upper()
                if (p_lower in ['apellido', 'apellidos', 'cedula', 'cédula', 'ci'] or
                    '/' in palabras[i] or
                    p_upper in estados_civiles):
                    break
                nombres_lista.append(palabras[i].upper())
                i += 1
            datos['nombres'] = ' '.join(nombres_lista)
            continue
        
        # Detectar fecha (contiene /)
        elif '/' in palabras[i]:
            match = re.match(r'^(\d{1,2}/\d{1,2}/\d{2,4})$', palabras[i])
            if match:
                datos['fecha_nac'] = palabras[i]
            i += 1
            continue
        
        # Detectar estado civil
        elif palabra_upper in estados_civiles:
            datos['estado_civil'] = palabra_upper
            i += 1
            continue
        
        else:
            i += 1
    
    # Verificar campos faltantes
    faltantes = [k for k, v in datos.items() if not v]
    
    if faltantes:
        return None, faltantes
    
    return datos, None


# --- HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for f in [ARCHIVO_FOTO, ARCHIVO_FIRMA]:
        if os.path.exists(f):
            os.remove(f)
    context.user_data.clear()
    
    await enviar_con_reintentos(
        update.message.reply_text,
        "¡Hola! Soy el bot para generar cédulas venezolanas.\n\n"
        "📋 *OPCIÓN 1: Rápida con /datos*\n"
        "Usa el comando `/datos` con las palabras clave:\n"
        "`/datos cedula 17.456.678 Apellido HERNÁNDEZ COVA Nombre JOSE ANGEL 23/07/1990 SOLTERO`\n\n"
        "🔑 *Palabras clave:* `cedula`, `Apellido`, `Nombre`, fecha con `/`, estado civil (SOLTERO, CASADO, etc.)\n\n"
        "📝 *OPCIÓN 2: Paso a paso*\n"
        "Envía el *número de cédula* (solo números):",
        parse_mode='Markdown'
    )
    return CEDULA


async def comando_datos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa el comando /datos con las palabras clave."""
    for f in [ARCHIVO_FOTO, ARCHIVO_FIRMA]:
        if os.path.exists(f):
            os.remove(f)
    context.user_data.clear()
    
    texto_original = update.message.text
    logger.info(f"Comando /datos recibido: {texto_original}")
    
    datos, faltantes = parsear_comando_datos(texto_original)
    
    if datos is None:
        faltantes_str = ', '.join([f"`{f}`" for f in faltantes])
        await update.message.reply_text(
            f"❌ *Faltan datos:* {faltantes_str}\n\n"
            "Usa el formato:\n"
            "`/datos cedula 17.456.678 Apellido HERNÁNDEZ COVA Nombre JOSE ANGEL 23/07/1990 SOLTERO`\n\n"
            "🔑 *Palabras clave:* `cedula`, `Apellido`, `Nombre`, fecha con `/`, estado civil.",
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    # Guardar datos en el contexto
    context.user_data.update(datos)
    logger.info(f"Datos parseados: {datos}")
    
    # Mostrar confirmación
    await enviar_con_reintentos(
        update.message.reply_text,
        f"✅ *Datos recibidos correctamente:*\n\n"
        f"🆔 Cédula: `{datos['cedula']}`\n"
        f"👤 Apellidos: `{datos['apellidos']}`\n"
        f"📛 Nombres: `{datos['nombres']}`\n"
        f"📅 Fecha Nac: `{datos['fecha_nac']}`\n"
        f"💍 Estado Civil: `{datos['estado_civil']}`\n\n"
        f"📸 Ahora envía la *FOTO* del titular:",
        parse_mode='Markdown'
    )
    return FOTO


# --- FLUJO PASO A PASO (alternativa) ---

async def cedula(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['cedula'] = update.message.text
    await enviar_con_reintentos(update.message.reply_text, "Envía los *APELLIDOS*:", parse_mode='Markdown')
    return APELLIDOS


async def apellidos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['apellidos'] = update.message.text.upper()
    await enviar_con_reintentos(update.message.reply_text, "Envía los *NOMBRES*:", parse_mode='Markdown')
    return NOMBRES


async def nombres(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nombres'] = update.message.text.upper()
    await enviar_con_reintentos(update.message.reply_text, "Envía la *FECHA DE NACIMIENTO* (DD/MM/AAAA):", parse_mode='Markdown')
    return FECHA_NAC


async def fecha_nac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['fecha_nac'] = update.message.text
    await enviar_con_reintentos(update.message.reply_text, "Envía el *ESTADO CIVIL*:", parse_mode='Markdown')
    return ESTADO_CIVIL


async def estado_civil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['estado_civil'] = update.message.text.upper()
    await enviar_con_reintentos(update.message.reply_text, "Envía la *FOTO* del titular (como imagen):", parse_mode='Markdown')
    return FOTO


# --- HANDLER COMÚN PARA FOTO Y FIRMA ---

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        foto = update.message.photo[-1]
        archivo = await foto.get_file()
        await archivo.download_to_drive(ARCHIVO_FOTO)
        logger.info(f"Foto descargada: {ARCHIVO_FOTO}")
    except Exception as e:
        logger.error(f"Error descargando foto: {e}")
        await enviar_con_reintentos(update.message.reply_text, f"❌ Error al recibir la foto: {e}")
        return FOTO
    
    boton = InlineKeyboardButton(
        "🖊️ Firmar aquí", 
        web_app=WebAppInfo(url=URL_EDITOR_FIRMA)
    )
    teclado = InlineKeyboardMarkup([[boton]])
    
    await enviar_con_reintentos(
        update.message.reply_text,
        "✅ Foto recibida.\n\n"
        "*AHORA LA FIRMA*\n\n"
        "Toca el botón, dibuja tu firma con el dedo y toca *Enviar Firma*.\n\n"
        "La firma llegará aquí automáticamente.",
        parse_mode='Markdown',
        reply_markup=teclado
    )
    return FIRMA


async def recibir_firma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("=== RECIBIENDO FIRMA ===")
    
    if not update.message.document:
        logger.warning("No es un documento, esperando firma...")
        await enviar_con_reintentos(
            update.message.reply_text,
            "Esperando la firma... Toca el botón de arriba para firmar."
        )
        return FIRMA
    
    try:
        doc = update.message.document
        logger.info(f"Documento recibido: {doc.file_name}")
        archivo = await doc.get_file()
        await archivo.download_to_drive(ARCHIVO_FIRMA)
        logger.info(f"Firma guardada en: {ARCHIVO_FIRMA}")
        
        await enviar_con_reintentos(
            update.message.reply_text,
            "✅ Firma recibida. Generando cédula... ⏳"
        )
        
        ruta = generar_cedula(context.user_data)
        
        with open(ruta, 'rb') as f:
            await enviar_con_reintentos(
                update.message.reply_photo,
                photo=f,
                caption="✅ ¡Aquí tienes tu cédula!"
            )
        
        logger.info("Cédula enviada con éxito")
        await enviar_con_reintentos(
            update.message.reply_text,
            "Escribe /start para generar otra."
        )
    except Exception as e:
        logger.error(f"Error generando cédula: {e}")
        import traceback
        traceback.print_exc()
        await enviar_con_reintentos(
            update.message.reply_text,
            f"❌ Error al generar: {e}"
        )
    
    return ConversationHandler.END


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Proceso cancelado.")
    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} causó error: {context.error}")
    try:
        if update and update.message:
            await update.message.reply_text(
                "⚠️ Hubo un error. Intenta de nuevo con /start"
            )
    except:
        pass


def main():
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    
    application = ApplicationBuilder().token(TOKEN).request(request).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            CommandHandler('datos', comando_datos),   # <-- Comando con palabras clave
        ],
        states={
            CEDULA: [MessageHandler(filters.TEXT & ~filters.COMMAND, cedula)],
            APELLIDOS: [MessageHandler(filters.TEXT & ~filters.COMMAND, apellidos)],
            NOMBRES: [MessageHandler(filters.TEXT & ~filters.COMMAND, nombres)],
            FECHA_NAC: [MessageHandler(filters.TEXT & ~filters.COMMAND, fecha_nac)],
            ESTADO_CIVIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, estado_civil)],
            FOTO: [MessageHandler(filters.PHOTO, recibir_foto)],
            FIRMA: [MessageHandler(filters.Document.ALL, recibir_firma)],
        },
        fallbacks=[CommandHandler('cancel', cancelar)],
        conversation_timeout=600
    )

    application.add_handler(conv)
    application.add_error_handler(error_handler)
    
    logger.info("🤖 Bot iniciado.")
    application.run_polling(
        poll_interval=1.0,
        timeout=30
    )


if __name__ == '__main__':
    main()