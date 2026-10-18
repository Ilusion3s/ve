from PIL import Image, ImageDraw, ImageFont
import os

# --- ARCHIVOS ---
PLANTILLA = 'plantilla.png'
SALIDA = '/sdcard/Download/prueba_textos.png'

# --- DATOS DEL TITULAR ---
datos = {
    'cedula': '9.283.265',
    'apellidos': 'GUZMAN ABACHE',
    'nombres': 'AVIER',
    'fecha_nac': '03/12/1959',
    'estado_civil': 'SOLTERO',
    'firma': 'Javier'
}

# --- COORDENADAS CAPTURADAS EN EL EDITOR ---
COORDENADAS = {
    'cedula':       {'x': 405, 'y': 182, 'tamano': 12},
    'apellidos':    {'x': 158, 'y': 221, 'tamano': 12},
    'nombres':      {'x': 157, 'y': 266, 'tamano': 12},
    'fecha_nac':    {'x': 266, 'y': 412, 'tamano': 12},
    'estado_civil': {'x': 432, 'y': 411, 'tamano': 14},
    'firma':        {'x': 76, 'y': 330, 'tamano': 20},
}

def cargar_fuente(tamano):
    rutas = [
        "/system/fonts/Roboto-Regular.ttf",
        "/system/fonts/DroidSans.ttf",
        "arial.ttf"
    ]
    for ruta in rutas:
        if os.path.exists(ruta):
            try:
                return ImageFont.truetype(ruta, tamano)
            except:
                pass
    return ImageFont.load_default()

def main():
    if not os.path.exists(PLANTILLA):
        print(f"❌ No se encuentra {PLANTILLA}")
        return

    img = Image.open(PLANTILLA).convert("RGB")
    draw = ImageDraw.Draw(img)
    
    ancho_real, alto_real = img.size
    print(f"📐 Dimensiones de la plantilla: {ancho_real} x {alto_real}")
    
    # Factor de escala: asumimos que el editor mostró la imagen a 400px de ancho
    # Si tu imagen real es de 1000px, el factor es 2.5
    factor_escala = ancho_real / 400.0
    print(f"📏 Factor de escala calculado: {factor_escala}")
    
    # Escribir cada texto
    for campo, coord in COORDENADAS.items():
        texto = datos[campo]
        tamano_real = int(coord['tamano'] * factor_escala)
        fuente = cargar_fuente(tamano_real)
        draw.text((coord['x'], coord['y']), texto, fill=(0,0,0), font=fuente)
        print(f"✍️ {campo}: '{texto}' en X={coord['x']}, Y={coord['y']}, Tamaño={tamano_real}")
    
    img.save(SALIDA)
    print(f"\n✅ Imagen guardada en: {SALIDA}")
    print("Ábrela en tu galería para verificar.")

if __name__ == '__main__':
    main()
