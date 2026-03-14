from flask import Flask, render_template, request, redirect, url_for, session, send_file, abort
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash
from collections import Counter
import resend
from functools import wraps
from datetime import datetime, timedelta
from dotenv import load_dotenv
import csv
import os
import re
import socket
import time
import base64
import threading

load_dotenv(override=True)
_csv_lock = threading.Lock()

resend.api_key = os.getenv('RESEND_API_KEY')

app = Flask(__name__)

app.secret_key = os.getenv('SECRET_KEY')
if not app.secret_key:
    raise RuntimeError("SECRET_KEY no definida en .env")

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=2)

CSV_FILE           = 'data/inscriptions.csv'
USERS_FILE         = 'data/users.csv'
UPLOAD_FOLDER      = 'data/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'pdf'}

MIME_SIGNATURES = {
    'png':  b'\x89PNG',
    'jpg':  b'\xff\xd8\xff',
    'jpeg': b'\xff\xd8\xff',
    'pdf':  b'%PDF',
}

ENCABEZADOS = ['nombre', 'cedula', 'email', 'telefono', 'genero', 'categoria', 'talla', 'fecha', 'pago', 'entrega']
app.config['UPLOAD_FOLDER']      = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024

os.makedirs('data', exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs('certificates/generated', exist_ok=True)

if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=ENCABEZADOS, delimiter=';')
        writer.writeheader()


_rate_store: dict = {}

def rate_limit(max_requests: int, window_seconds: int):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            ip  = request.remote_addr or 'unknown'
            now = time.time()
            bucket = _rate_store.get(ip, [])
            bucket = [t for t in bucket if now - t < window_seconds]
            if len(bucket) >= max_requests:
                return render_template('error.html', mensaje="Demasiadas solicitudes. Espera un momento e inténtalo de nuevo."), 429
            bucket.append(now)
            _rate_store[ip] = bucket
            return func(*args, **kwargs)
        return wrapper
    return decorator


@app.after_request
def agregar_headers_seguridad(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' cdnjs.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' fonts.googleapis.com cdnjs.cloudflare.com; "
    "font-src fonts.gstatic.com cdnjs.cloudflare.com; "
    "img-src 'self' data: blob: images.unsplash.com upload.wikimedia.org i.vimeocdn.com vumbnail.com; "
    "media-src 'self'; "
    "frame-src 'self' https://drive.google.com; "
    "connect-src 'self' cdnjs.cloudflare.com blob: https://drive.google.com; "
    "worker-src blob:;"
    )
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def verificar_contenido_archivo(file_storage) -> bool:
    ext = file_storage.filename.rsplit('.', 1)[1].lower() if '.' in file_storage.filename else ''
    signature = MIME_SIGNATURES.get(ext)
    if not signature:
        return False
    header = file_storage.read(len(signature))
    file_storage.seek(0)
    return header == signature


def cedula_segura(cedula: str) -> str:
    if not cedula or not re.fullmatch(r'\d{10}', cedula):
        abort(400)
    return cedula


def validar_datos_formulario(form):
    errores = []
    nombre = form.get('nombre', '').strip()
    if not nombre or not re.match(r'^[A-Za-zñÑáéíóúÁÉÍÓÚ\s]+$', nombre):
        errores.append("El nombre debe contener solo letras y espacios.")
    cedula = form.get('cedula', '').strip()
    if not cedula.isdigit() or len(cedula) != 10:
        errores.append("La cédula debe tener exactamente 10 dígitos numéricos.")
    email = form.get('email', '').strip()
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        errores.append("El formato del correo electrónico no es válido.")
    telefono = form.get('telefono', '').strip()
    if not telefono.isdigit() or len(telefono) != 10:
        errores.append("El teléfono debe tener exactamente 10 dígitos numéricos.")
    if not form.get('categoria'):
        errores.append("Debes seleccionar una distancia y una categoría válida.")
    if not form.get('talla'):
        errores.append("Debes seleccionar una talla de camiseta.")
    return errores


def leer_csv():
    if not os.path.exists(CSV_FILE):
        return []
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter=';'))


def guardar_csv(filas):
    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=ENCABEZADOS, delimiter=';')
        writer.writeheader()
        writer.writerows(filas)


def modificar_csv(func):
    with _csv_lock:
        filas = leer_csv()
        func(filas)
        guardar_csv(filas)


def buscar_corredor(cedula):
    cedula = cedula_segura(cedula)
    for row in leer_csv():
        if row.get('cedula') == cedula:
            return row
    return None


def cedula_ya_registrada(cedula):
    return buscar_corredor(cedula) is not None


def enviar_email_confirmacion(destinatario, nombre, cedula):
    try:
        link_certificado = url_for('ver_certificado', cedula=cedula, _external=True)
        texto_wa      = f"Estoy inscrito en Trail Running 2026 Del Bosque al Mar. Mi certificado: {link_certificado}"
        link_whatsapp = f"https://wa.me/?text={texto_wa.replace(' ', '%20')}"

        params = {
            "from": "Trail Running 2026 <onboarding@resend.dev>",
            "to": [destinatario],
            "subject": "¡Inscripción Confirmada! - Trail Running 2026",
            "html": f"""
        <html>
        <body style="font-family:Arial,sans-serif;background:#0f172a;color:white;padding:20px;">
          <div style="max-width:600px;margin:0 auto;background:#1e293b;padding:30px;
                      border-radius:10px;border:1px solid #ccff00;">
            <h2 style="color:#ccff00;text-align:center;text-transform:uppercase;">
              ¡INSCRIPCIÓN CONFIRMADA!
            </h2>
            <p style="color:#cbd5e1;font-size:16px;">
              Hola <strong style="color:white;">{nombre}</strong>,
            </p>
            <p style="color:#cbd5e1;font-size:16px;line-height:1.5;">
              Hemos verificado tu pago. Tu lugar en la línea de partida está asegurado.
            </p>
            <div style="text-align:center;margin:40px 0;">
              <a href="{link_certificado}"
                 style="background:#ccff00;color:black;padding:15px 30px;
                        text-decoration:none;font-weight:bold;border-radius:5px;
                        font-size:18px;display:inline-block;">
                VER MI CERTIFICADO OFICIAL
              </a>
              <br><br>
              <a href="{link_whatsapp}"
                 style="background:#25d366;color:white;padding:12px 24px;
                        text-decoration:none;font-weight:bold;border-radius:5px;
                        font-size:15px;display:inline-block;">
                📲 COMPARTIR EN WHATSAPP
              </a>
            </div>
            <p style="color:#94a3b8;font-size:14px;text-align:center;">
              Guarda este correo. Es tu pase digital para el evento y la entrega de kits.
            </p>
            <div style="text-align:center;margin-top:30px;padding-top:20px;
                        border-top:1px solid rgba(255,255,255,0.1);">
              <p style="color:#94a3b8;font-size:14px;margin:0;">¡Nos vemos en la ruta!</p>
              <strong style="color:white;font-size:16px;">Equipo Trail Running 2026</strong>
            </div>
          </div>
        </body>
        </html>"""
        }

        resend.Emails.send(params)
        return True
    except Exception as e:
        print(f"[ERROR] Enviando correo a {destinatario}: {e}")
        return False

def authenticate(username, password):
    if not os.path.exists(USERS_FILE):
        return None
    with open(USERS_FILE, newline='', encoding='utf-8') as f:
        for user in csv.DictReader(f):
            if user['username'] == username:
                stored = user['password']
                if stored.startswith('pbkdf2:') or stored.startswith('scrypt:'):
                    if check_password_hash(stored, password):
                        return user['role']
                else:
                    if stored == password:
                        return user['role']
    return None


def login_required(role=None):
    def wrapper(func):
        @wraps(func)
        def decorated(*args, **kwargs):
            if 'user' not in session:
                return redirect(url_for('admin_login'))
            if role and session.get('role') != role:
                return render_template('error.html', mensaje="No tienes permisos para esta sección."), 403
            return func(*args, **kwargs)
        return decorated
    return wrapper


@app.errorhandler(RequestEntityTooLarge)
def archivo_muy_grande(e):
    return render_template('error.html', mensaje="El archivo supera el límite de 5 MB."), 413

@app.errorhandler(404)
def pagina_no_encontrada(e):
    return render_template('error.html', mensaje="Página no encontrada."), 404

@app.errorhandler(500)
def error_interno(e):
    return render_template('error.html', mensaje="Error interno del servidor."), 500

@app.errorhandler(429)
def demasiadas_solicitudes(e):
    return render_template('error.html', mensaje="Demasiadas solicitudes. Espera un momento."), 429


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=600)
def register():
    errores = validar_datos_formulario(request.form)
    if errores:
        return render_template('error.html', mensaje="Revisa los siguientes campos:", errores=errores), 400

    cedula = request.form['cedula'].strip()

    corredor = buscar_corredor(cedula)
    if corredor:
        pago = corredor.get('pago', '').upper()
        if pago in ('SI', 'PAGADO', 'VERIFICADO'):
            return render_template('success_pago.html', nombre=corredor['nombre'], cedula=cedula)
        return redirect(url_for('payment', cedula=cedula))

    nuevo = {
        'nombre':    request.form['nombre'].strip().upper(),
        'cedula':    cedula,
        'email':     request.form['email'].strip().lower(),
        'telefono':  request.form['telefono'].strip(),
        'genero':    request.form.get('genero', '').strip().upper(),
        'categoria': request.form['categoria'].strip().upper(),
        'talla':     request.form['talla'],
        'fecha':     datetime.now().strftime('%Y-%m-%d %H:%M'),
        'pago':      'NO',
        'entrega':   'NO'
    }

    ya_existia = False

    def _agregar(filas):
        nonlocal ya_existia
        if any(f['cedula'] == cedula for f in filas):
            ya_existia = True
            return
        filas.append(nuevo)

    modificar_csv(_agregar)

    if ya_existia:
        corredor = buscar_corredor(cedula)
        pago = corredor.get('pago', '').upper() if corredor else ''
        if pago in ('SI', 'PAGADO', 'VERIFICADO'):
            return render_template('success_pago.html', nombre=corredor['nombre'], cedula=cedula)
        return redirect(url_for('payment', cedula=cedula))

    return redirect(url_for('payment', cedula=cedula))


@app.route('/payment/<cedula>', methods=['GET', 'POST'])
@rate_limit(max_requests=5, window_seconds=300)
def payment(cedula):
    cedula   = cedula_segura(cedula)
    corredor = buscar_corredor(cedula)
    if not corredor:
        return render_template('error.html', mensaje="Cédula no encontrada en el sistema."), 404

    if request.method == 'POST':
        if 'comprobante' not in request.files:
            return render_template('error.html', mensaje="No se recibió ningún archivo."), 400
        file = request.files['comprobante']
        if file.filename == '':
            return render_template('error.html', mensaje="No seleccionaste ningún archivo."), 400
        if not allowed_file(file.filename):
            return render_template('error.html', mensaje="Formato no permitido. Sube PNG, JPG o PDF."), 400
        if not verificar_contenido_archivo(file):
            return render_template('error.html', mensaje="El archivo no corresponde al tipo declarado."), 400

        ext      = secure_filename(file.filename).rsplit('.', 1)[1].lower()
        filename = f"comprobante_{cedula}.{ext}"
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        def _marcar_revision(filas):
            for row in filas:
                if row['cedula'] == cedula:
                    row['pago'] = 'REVISION'

        modificar_csv(_marcar_revision)
        return render_template('payment_success.html', nombre=corredor['nombre'])

    return render_template('payment.html', cedula=cedula, nombre=corredor['nombre'])


@app.route('/check', methods=['GET', 'POST'])
@rate_limit(max_requests=15, window_seconds=600)
def check_registration():
    error = None
    if request.method == 'POST':
        cedula = request.form.get('cedula', '').strip()
        if not cedula.isdigit() or len(cedula) != 10:
            error = "Ingresa una cédula válida de 10 dígitos."
        else:
            corredor = buscar_corredor(cedula)
            if corredor:
                pago = corredor.get('pago', '').upper()
                if pago in ('SI', 'PAGADO', 'VERIFICADO'):
                    return redirect(url_for('ver_certificado', cedula=cedula))
                else:
                    return redirect(url_for('payment', cedula=cedula))
            else:
                error = "Esa cédula no aparece en nuestros registros."
    return render_template('check.html', error=error)


@app.route('/certificado/<cedula>')
def ver_certificado(cedula):
    cedula   = cedula_segura(cedula)
    corredor = buscar_corredor(cedula)
    if not corredor:
        return render_template('error.html', mensaje="Atleta no encontrado."), 404

    fondo_b64 = ''
    fondo_path = os.path.join('static', 'img', 'fondo_certificado_oficial.jpg')
    if os.path.exists(fondo_path):
        with open(fondo_path, 'rb') as f:
            fondo_b64 = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode()

    url_validacion   = url_for('validar_kit', cedula=corredor['cedula'], _external=True)
    url_cert_externo = url_for('ver_certificado', cedula=cedula, _external=True)
    texto_wa         = f"Estoy inscrito en Trail Running 2026 Del Bosque al Mar. Mi certificado: {url_cert_externo}"
    link_whatsapp    = f"https://wa.me/?text={texto_wa.replace(' ', '%20')}"

    return render_template('certificado.html', corredor=corredor,
                           url_validacion=url_validacion,
                           link_whatsapp=link_whatsapp,
                           fondo_b64=fondo_b64)


@app.route('/success/<cedula>')
def descarga_exito(cedula):
    cedula   = cedula_segura(cedula)
    pdf_path = f'certificates/generated/{cedula}.pdf'
    if not os.path.exists(pdf_path):
        return render_template('error.html', mensaje="Certificado aún no generado. Espera la confirmación de tu pago."), 404
    return render_template('success_download.html', cedula=cedula)


@app.route('/download/public/<cedula>')
def download_public_certificate(cedula):
    cedula    = cedula_segura(cedula)
    file_path = f'certificates/generated/{cedula}.pdf'
    if not os.path.exists(file_path):
        return render_template('error.html', mensaje="Certificado no encontrado."), 404
    return send_file(file_path, as_attachment=True, download_name=f'Certificado_Trail_{cedula}.pdf')


@app.route('/admin', methods=['GET', 'POST'])
@rate_limit(max_requests=5, window_seconds=900)
def admin_login():
    error = None
    if request.method == 'POST':
        role = authenticate(request.form.get('username', ''), request.form.get('password', ''))
        if role:
            session.permanent = True
            session['user']       = request.form['username']
            session['role']       = role
            session['login_time'] = datetime.now().isoformat()
            return redirect(url_for('dashboard'))
        error = "Usuario o contraseña incorrectos."
    return render_template('admin/login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin_login'))


@app.route('/admin/dashboard')
@login_required()
def dashboard():
    inscritos  = leer_csv()
    total      = len(inscritos)
    pagados    = sum(1 for i in inscritos if i.get('pago', '').upper() in ('SI', 'PAGADO', 'VERIFICADO'))
    revision   = sum(1 for i in inscritos if i.get('pago', '').upper() == 'REVISION')
    pendientes = total - pagados - revision
    tallas     = dict(sorted(Counter(i['talla']     for i in inscritos if i.get('talla')).items()))
    categorias = dict(sorted(Counter(i['categoria'] for i in inscritos if i.get('categoria')).items()))
    return render_template(
        'admin/dashboard.html',
        inscritos=inscritos,
        total=total,
        pagados=pagados,
        revision=revision,
        pendientes=pendientes,
        tallas=tallas,
        categorias=categorias,
        role=session.get('role')
    )


@app.route('/admin/marcar_pagado/<cedula>', methods=['POST'])
@login_required()
def marcar_pagado(cedula):
    cedula     = cedula_segura(cedula)
    email_dest = nombre_dest = ''

    def _marcar_pagado(filas):
        nonlocal email_dest, nombre_dest
        for row in filas:
            if row['cedula'] == cedula:
                row['pago']  = 'SI'
                email_dest   = row['email']
                nombre_dest  = row['nombre']

    modificar_csv(_marcar_pagado)

    if email_dest:
        if not enviar_email_confirmacion(email_dest, nombre_dest, cedula):
            print(f"[AVISO] No se pudo enviar email a {email_dest}")
    return redirect(url_for('dashboard'))


@app.route('/admin/ver_comprobante/<cedula>')
@login_required()
def ver_comprobante(cedula):
    cedula = cedula_segura(cedula)
    for ext in ALLOWED_EXTENSIONS:
        path = os.path.join(UPLOAD_FOLDER, f"comprobante_{cedula}.{ext}")
        if os.path.exists(path):
            return send_file(path)
    return render_template('error.html', mensaje="Comprobante no encontrado (quizás aún no lo subió)."), 404


@app.route('/admin/delete/<cedula>', methods=['POST'])
@login_required('admin')
def eliminar_inscripcion(cedula):
    cedula = cedula_segura(cedula)

    def _eliminar(filas):
        filas[:] = [f for f in filas if f['cedula'] != cedula]

    modificar_csv(_eliminar)

    pdf = f'certificates/generated/{cedula}.pdf'
    if os.path.exists(pdf):
        try:
            os.remove(pdf)
        except OSError as e:
            print(f"[AVISO] No se pudo borrar el PDF de {cedula}: {e}")
    return redirect(url_for('dashboard'))


@app.route('/admin/export')
@login_required('admin')
def export_csv():
    print(f"[AUDIT] CSV exportado por '{session.get('user')}' el {datetime.now().isoformat()}")
    return send_file(CSV_FILE, as_attachment=True, download_name='inscripciones_trail2026.csv')


@app.route('/admin/certificate/<cedula>')
@login_required()
def download_certificate(cedula):
    cedula    = cedula_segura(cedula)
    file_path = f'certificates/generated/{cedula}.pdf'
    if not os.path.exists(file_path):
        return render_template('error.html', mensaje="Certificado no encontrado."), 404
    return send_file(file_path, as_attachment=True)


@app.route('/admin/estadisticas')
@login_required()
def estadisticas():
    inscritos  = leer_csv()
    tallas     = dict(sorted(Counter(i['talla']     for i in inscritos if i.get('talla')).items()))
    categorias = dict(sorted(Counter(i['categoria'] for i in inscritos if i.get('categoria')).items()))
    return render_template('admin/estadisticas.html', tallas=tallas, categorias=categorias)


@app.route('/validar/<cedula>')
def validar_kit(cedula):
    cedula    = cedula_segura(cedula)
    inscritos = leer_csv()
    corredor  = next((i for i in inscritos if i['cedula'] == cedula), None)

    pagados_total    = [i for i in inscritos if i.get('pago', '').upper() in ('SI', 'PAGADO', 'VERIFICADO')]
    entregados_count = sum(1 for i in pagados_total if i.get('entrega') == 'SI')

    if not corredor:
        return render_template('admin/validador.html', estado='error', mensaje='CÉDULA NO ENCONTRADA', corredor=None)

    pago = corredor.get('pago', '').upper()
    if pago in ('SI', 'PAGADO', 'VERIFICADO'):
        return render_template('admin/validador.html', estado='success', corredor=corredor,
                               entregados=entregados_count, total_pagados=len(pagados_total))
    return render_template('admin/validador.html', estado='pendiente', corredor=corredor,
                           entregados=entregados_count, total_pagados=len(pagados_total))


@app.route('/admin/confirmar_entrega/<cedula>', methods=['POST'])
def confirmar_entrega(cedula):
    cedula = cedula_segura(cedula)

    def _confirmar(filas):
        for row in filas:
            if row['cedula'] == cedula:
                row['entrega'] = 'SI'

    modificar_csv(_confirmar)
    return redirect(url_for('validar_kit', cedula=cedula) + '?scan=1')


@app.route('/admin/buscar_manual', methods=['GET', 'POST'])
def buscar_manual():
    if request.method == 'POST':
        cedula = request.form.get('cedula', '').strip()
        cedula = cedula_segura(cedula)
        return redirect(url_for('validar_kit', cedula=cedula))
    return render_template('admin/buscar_manual.html')


@app.route('/admin/editar/<cedula>', methods=['POST'])
@login_required()
def editar_inscripcion(cedula):
    cedula = cedula_segura(cedula)

    def _editar(filas):
        for row in filas:
            if row['cedula'] == cedula:
                row['nombre']    = request.form.get('nombre',    row['nombre']).strip().upper()
                row['email']     = request.form.get('email',     row['email']).strip().lower()
                row['telefono']  = request.form.get('telefono',  row['telefono']).strip()
                row['categoria'] = request.form.get('categoria', row['categoria']).strip().upper()
                row['talla']     = request.form.get('talla',     row['talla']).strip()

    modificar_csv(_editar)
    return redirect(url_for('dashboard'))


def obtener_ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    ip_local   = obtener_ip_local()
    print(f"\n Servidor arrancando en http://{ip_local}:5000")
    print(f"   Modo debug: {'ACTIVADO' if debug_mode else 'DESACTIVADO'}\n")
    app.run(debug=debug_mode, host='0.0.0.0')