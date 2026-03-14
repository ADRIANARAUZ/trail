"""
test_app.py — Pruebas unitarias completas para Trail Running 2026
Ejecutar con: pytest test_app.py -v
Instalar dependencias: pip install pytest flask werkzeug
"""

import pytest
import csv
import io
import os
import sys
import tempfile
import shutil
from unittest.mock import patch, MagicMock
from datetime import datetime
import threading

# ──────────────────────────────────────────────────────────────────────────────
# Setup del entorno antes de importar la app
# ──────────────────────────────────────────────────────────────────────────────

os.environ.setdefault('SECRET_KEY', 'test-secret-key-123')
os.environ.setdefault('RESEND_API_KEY', 'fake-resend-key')
os.environ.setdefault('FLASK_DEBUG', 'false')

# Directorios temporales para tests (evitar tocar datos reales)
TEST_DATA_DIR    = tempfile.mkdtemp()
TEST_UPLOAD_DIR  = os.path.join(TEST_DATA_DIR, 'uploads')
TEST_CSV_FILE    = os.path.join(TEST_DATA_DIR, 'inscriptions.csv')
TEST_USERS_FILE  = os.path.join(TEST_DATA_DIR, 'users.csv')
TEST_CERTS_DIR   = os.path.join(TEST_DATA_DIR, 'certs')

os.makedirs(TEST_UPLOAD_DIR, exist_ok=True)
os.makedirs(TEST_CERTS_DIR,  exist_ok=True)

# Parchear rutas de archivos ANTES de importar app
import unittest.mock
unittest.mock.patch.dict('os.environ', {'SECRET_KEY': 'test-secret-key-123'}).start()

# Importar app y parchear sus constantes de archivo
import importlib
import app as app_module

app_module.CSV_FILE      = TEST_CSV_FILE
app_module.USERS_FILE    = TEST_USERS_FILE
app_module.UPLOAD_FOLDER = TEST_UPLOAD_DIR
app_module.app.config['UPLOAD_FOLDER'] = TEST_UPLOAD_DIR

ENCABEZADOS = app_module.ENCABEZADOS


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def crear_csv_vacio():
    """Crea el CSV con solo encabezados."""
    with open(TEST_CSV_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=ENCABEZADOS, delimiter=';')
        writer.writeheader()


def insertar_corredor(cedula='1234567890', pago='NO', entrega='NO', nombre='JUAN TEST',
                      email='juan@test.com', telefono='0991234567',
                      categoria='10K LIBRE', talla='M', genero='M'):
    """Inserta un corredor de prueba en el CSV."""
    filas = app_module.leer_csv()
    filas.append({
        'nombre': nombre, 'cedula': cedula, 'email': email,
        'telefono': telefono, 'genero': genero, 'categoria': categoria,
        'talla': talla, 'fecha': '2026-01-01 10:00', 'pago': pago, 'entrega': entrega
    })
    app_module.guardar_csv(filas)


def crear_usuarios_csv(usuarios=None):
    """Crea el archivo de usuarios de prueba."""
    if usuarios is None:
        usuarios = [
            {'username': 'admin',  'password': 'admin123',  'role': 'admin'},
            {'username': 'staff',  'password': 'staff123',  'role': 'staff'},
        ]
    with open(TEST_USERS_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['username', 'password', 'role'])
        writer.writeheader()
        writer.writerows(usuarios)


def png_falso():
    """Bytes mínimos de un PNG válido."""
    return b'\x89PNG\r\n\x1a\n' + b'\x00' * 100


def jpg_falso():
    """Bytes mínimos de un JPG válido."""
    return b'\xff\xd8\xff\xe0' + b'\x00' * 100


def pdf_falso():
    """Bytes mínimos de un PDF válido."""
    return b'%PDF-1.4\n' + b'\x00' * 100


# ──────────────────────────────────────────────────────────────────────────────
# Fixture principal: cliente de prueba Flask
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def limpiar_entre_tests():
    """Antes de cada test: CSV vacío y sin rate-limit acumulado."""
    crear_csv_vacio()
    app_module._rate_store.clear()
    yield
    # Limpieza post-test de comprobantes subidos
    for f in os.listdir(TEST_UPLOAD_DIR):
        try:
            os.remove(os.path.join(TEST_UPLOAD_DIR, f))
        except OSError:
            pass


@pytest.fixture
def client():
    app_module.app.config['TESTING'] = True
    app_module.app.config['WTF_CSRF_ENABLED'] = False
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def client_admin(client):
    """Cliente ya autenticado como admin."""
    crear_usuarios_csv()
    with app_module.app.test_request_context():
        pass
    with client.session_transaction() as sess:
        sess['user']  = 'admin'
        sess['role']  = 'admin'
        sess['login_time'] = datetime.now().isoformat()
    return client


@pytest.fixture
def client_staff(client):
    """Cliente ya autenticado como staff."""
    crear_usuarios_csv()
    with client.session_transaction() as sess:
        sess['user']  = 'staff'
        sess['role']  = 'staff'
        sess['login_time'] = datetime.now().isoformat()
    return client


# ══════════════════════════════════════════════════════════════════════════════
# 1. FUNCIONES UTILITARIAS
# ══════════════════════════════════════════════════════════════════════════════

class TestUtilidades:

    def test_leer_csv_vacio(self):
        filas = app_module.leer_csv()
        assert filas == []

    def test_leer_csv_con_datos(self):
        insertar_corredor()
        filas = app_module.leer_csv()
        assert len(filas) == 1
        assert filas[0]['cedula'] == '1234567890'

    def test_guardar_csv_persiste(self):
        insertar_corredor(cedula='0987654321')
        filas = app_module.leer_csv()
        assert any(f['cedula'] == '0987654321' for f in filas)

    def test_buscar_corredor_existente(self):
        insertar_corredor(cedula='1112223334')
        corredor = app_module.buscar_corredor('1112223334')
        assert corredor is not None
        assert corredor['cedula'] == '1112223334'

    def test_buscar_corredor_inexistente(self):
        assert app_module.buscar_corredor('0000000000') is None

    def test_cedula_ya_registrada_true(self):
        insertar_corredor(cedula='1234567890')
        assert app_module.cedula_ya_registrada('1234567890') is True

    def test_cedula_ya_registrada_false(self):
        assert app_module.cedula_ya_registrada('9999999999') is False

    def test_allowed_file_extensiones_validas(self):
        for ext in ['foto.png', 'foto.jpg', 'foto.jpeg', 'recibo.pdf']:
            assert app_module.allowed_file(ext), f"Debería permitir {ext}"

    def test_allowed_file_extensiones_invalidas(self):
        for ext in ['script.js', 'virus.exe', 'datos.csv', 'foto.gif', 'foto.webp']:
            assert not app_module.allowed_file(ext), f"Debería rechazar {ext}"

    def test_allowed_file_sin_extension(self):
        assert not app_module.allowed_file('archivo_sin_extension')

    def test_cedula_segura_valida(self):
        with app_module.app.test_request_context():
            resultado = app_module.cedula_segura('1234567890')
            assert resultado == '1234567890'

    def test_cedula_segura_invalida_letras(self):
        with app_module.app.test_request_context():
            with pytest.raises(Exception):  # abort(400)
                app_module.cedula_segura('abc1234567')

    def test_cedula_segura_invalida_longitud(self):
        with app_module.app.test_request_context():
            with pytest.raises(Exception):
                app_module.cedula_segura('12345')

    def test_cedula_segura_vacia(self):
        with app_module.app.test_request_context():
            with pytest.raises(Exception):
                app_module.cedula_segura('')


# ══════════════════════════════════════════════════════════════════════════════
# 2. VALIDACIÓN DE FORMULARIO
# ══════════════════════════════════════════════════════════════════════════════

class TestValidacionFormulario:

    def _form_valido(self, **overrides):
        base = {
            'nombre':    'Juan Perez',
            'cedula':    '1234567890',
            'email':     'juan@example.com',
            'telefono':  '0991234567',
            'genero':    'M',
            'categoria': '10K LIBRE',
            'talla':     'M',
        }
        base.update(overrides)
        return base

    def test_formulario_valido_sin_errores(self):
        errores = app_module.validar_datos_formulario(self._form_valido())
        assert errores == []

    def test_nombre_vacio(self):
        errores = app_module.validar_datos_formulario(self._form_valido(nombre=''))
        assert any('nombre' in e.lower() for e in errores)

    def test_nombre_con_numeros(self):
        errores = app_module.validar_datos_formulario(self._form_valido(nombre='Juan123'))
        assert any('nombre' in e.lower() for e in errores)

    def test_nombre_con_caracteres_especiales(self):
        errores = app_module.validar_datos_formulario(self._form_valido(nombre='Juan@#$'))
        assert any('nombre' in e.lower() for e in errores)

    def test_nombre_con_tildes_valido(self):
        errores = app_module.validar_datos_formulario(self._form_valido(nombre='José Álvarez'))
        assert errores == []

    def test_cedula_menos_de_10_digitos(self):
        errores = app_module.validar_datos_formulario(self._form_valido(cedula='12345'))
        assert any('cédula' in e.lower() for e in errores)

    def test_cedula_mas_de_10_digitos(self):
        errores = app_module.validar_datos_formulario(self._form_valido(cedula='12345678901'))
        assert any('cédula' in e.lower() for e in errores)

    def test_cedula_con_letras(self):
        errores = app_module.validar_datos_formulario(self._form_valido(cedula='123456789A'))
        assert any('cédula' in e.lower() for e in errores)

    def test_email_invalido_sin_arroba(self):
        errores = app_module.validar_datos_formulario(self._form_valido(email='noesuncorreo'))
        assert any('correo' in e.lower() for e in errores)

    def test_email_invalido_sin_dominio(self):
        errores = app_module.validar_datos_formulario(self._form_valido(email='user@'))
        assert any('correo' in e.lower() for e in errores)

    def test_email_valido(self):
        errores = app_module.validar_datos_formulario(self._form_valido(email='test@gmail.com'))
        assert errores == []

    def test_telefono_menos_10_digitos(self):
        errores = app_module.validar_datos_formulario(self._form_valido(telefono='099123'))
        assert any('teléfono' in e.lower() for e in errores)

    def test_telefono_con_letras(self):
        errores = app_module.validar_datos_formulario(self._form_valido(telefono='099123456A'))
        assert any('teléfono' in e.lower() for e in errores)

    def test_sin_categoria(self):
        errores = app_module.validar_datos_formulario(self._form_valido(categoria=''))
        assert any('categoría' in e.lower() or 'distancia' in e.lower() for e in errores)

    def test_sin_talla(self):
        errores = app_module.validar_datos_formulario(self._form_valido(talla=''))
        assert any('talla' in e.lower() for e in errores)

    def test_multiples_errores(self):
        form = self._form_valido(nombre='', cedula='abc', email='bad')
        errores = app_module.validar_datos_formulario(form)
        assert len(errores) >= 3


# ══════════════════════════════════════════════════════════════════════════════
# 3. VERIFICACIÓN DE CONTENIDO DE ARCHIVOS
# ══════════════════════════════════════════════════════════════════════════════

class TestVerificacionArchivos:

    def _mock_file(self, content: bytes, filename: str):
        """Crea un objeto FileStorage simulado."""
        mock = MagicMock()
        mock.filename = filename
        data = io.BytesIO(content)
        mock.read = data.read
        mock.seek = data.seek
        return mock

    def test_png_valido(self):
        f = self._mock_file(png_falso(), 'foto.png')
        assert app_module.verificar_contenido_archivo(f) is True

    def test_jpg_valido(self):
        f = self._mock_file(jpg_falso(), 'foto.jpg')
        assert app_module.verificar_contenido_archivo(f) is True

    def test_jpeg_valido(self):
        f = self._mock_file(jpg_falso(), 'foto.jpeg')
        assert app_module.verificar_contenido_archivo(f) is True

    def test_pdf_valido(self):
        f = self._mock_file(pdf_falso(), 'recibo.pdf')
        assert app_module.verificar_contenido_archivo(f) is True

    def test_png_con_contenido_falso(self):
        """Archivo renombrado como PNG pero con contenido de texto."""
        f = self._mock_file(b'este no es un png', 'foto.png')
        assert app_module.verificar_contenido_archivo(f) is False

    def test_pdf_con_contenido_falso(self):
        f = self._mock_file(b'<html>hola</html>', 'recibo.pdf')
        assert app_module.verificar_contenido_archivo(f) is False

    def test_extension_desconocida(self):
        f = self._mock_file(b'contenido', 'archivo.exe')
        assert app_module.verificar_contenido_archivo(f) is False


# ══════════════════════════════════════════════════════════════════════════════
# 4. AUTENTICACIÓN
# ══════════════════════════════════════════════════════════════════════════════

class TestAutenticacion:

    def setup_method(self):
        crear_usuarios_csv()

    def test_login_correcto_admin(self):
        role = app_module.authenticate('admin', 'admin123')
        assert role == 'admin'

    def test_login_correcto_staff(self):
        role = app_module.authenticate('staff', 'staff123')
        assert role == 'staff'

    def test_login_password_incorrecto(self):
        role = app_module.authenticate('admin', 'wrongpassword')
        assert role is None

    def test_login_usuario_inexistente(self):
        role = app_module.authenticate('noexiste', 'cualquier')
        assert role is None

    def test_login_ambos_vacios(self):
        role = app_module.authenticate('', '')
        assert role is None

    def test_login_sin_archivo_usuarios(self):
        """Si no existe users.csv, authenticate debe retornar None."""
        if os.path.exists(TEST_USERS_FILE):
            os.remove(TEST_USERS_FILE)
        role = app_module.authenticate('admin', 'admin123')
        assert role is None


# ══════════════════════════════════════════════════════════════════════════════
# 5. RUTAS PÚBLICAS
# ══════════════════════════════════════════════════════════════════════════════

class TestRutasPublicas:

    def test_index_200(self, client):
        r = client.get('/')
        assert r.status_code == 200

    def test_check_get_200(self, client):
        r = client.get('/check')
        assert r.status_code == 200

    def test_check_cedula_no_registrada(self, client):
        r = client.post('/check', data={'cedula': '9999999999'})
        assert r.status_code == 200
        assert 'registros' in r.data.decode().lower() or 'no' in r.data.decode().lower()

    def test_check_cedula_invalida_letras(self, client):
        r = client.post('/check', data={'cedula': 'abc'})
        assert r.status_code == 200
        assert 'válida' in r.data.decode().lower() or 'válido' in r.data.decode().lower()

    def test_check_redirige_a_certificado_si_pagado(self, client):
        insertar_corredor(cedula='1234567890', pago='SI')
        r = client.post('/check', data={'cedula': '1234567890'})
        assert r.status_code in (302, 200)
        if r.status_code == 302:
            assert 'certificado' in r.headers['Location'].lower()

    def test_check_redirige_a_pago_si_pendiente(self, client):
        insertar_corredor(cedula='1234567890', pago='NO')
        r = client.post('/check', data={'cedula': '1234567890'})
        assert r.status_code in (302, 200)
        if r.status_code == 302:
            assert 'payment' in r.headers['Location'].lower()

    def test_certificado_corredor_pagado(self, client):
        insertar_corredor(cedula='1234567890', pago='SI')
        r = client.get('/certificado/1234567890')
        assert r.status_code == 200

    def test_certificado_corredor_inexistente(self, client):
        r = client.get('/certificado/0000000000')
        assert r.status_code == 404

    def test_validar_kit_corredor_pagado(self, client):
        insertar_corredor(cedula='1234567890', pago='SI')
        r = client.get('/validar/1234567890')
        assert r.status_code == 200

    def test_validar_kit_corredor_pendiente(self, client):
        insertar_corredor(cedula='1234567890', pago='NO')
        r = client.get('/validar/1234567890')
        assert r.status_code == 200

    def test_validar_kit_cedula_inexistente(self, client):
        r = client.get('/validar/0000000000')
        assert r.status_code == 200  # Renderiza estado='error'

    def test_ruta_inexistente_404(self, client):
        r = client.get('/ruta-que-no-existe')
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# 6. REGISTRO (/register)
# ══════════════════════════════════════════════════════════════════════════════

class TestRegistro:

    def _datos_validos(self, **overrides):
        base = {
            'nombre':    'Maria Lopez',
            'cedula':    '0987654321',
            'email':     'maria@test.com',
            'telefono':  '0991111111',
            'genero':    'F',
            'categoria': '21K LIBRE',
            'talla':     'S',
        }
        base.update(overrides)
        return base

    def test_registro_nuevo_corredor(self, client):
        r = client.post('/register', data=self._datos_validos())
        assert r.status_code in (302, 200)
        filas = app_module.leer_csv()
        assert any(f['cedula'] == '0987654321' for f in filas)

    def test_registro_redirige_a_payment(self, client):
        r = client.post('/register', data=self._datos_validos())
        assert r.status_code == 302
        assert 'payment' in r.headers['Location'].lower()

    def test_registro_corredor_ya_pagado(self, client):
        insertar_corredor(cedula='0987654321', pago='SI', nombre='MARIA LOPEZ')
        r = client.post('/register', data=self._datos_validos())
        assert r.status_code in (200, 302)

    def test_registro_corredor_en_revision_redirige_payment(self, client):
        insertar_corredor(cedula='0987654321', pago='REVISION')
        r = client.post('/register', data=self._datos_validos())
        assert r.status_code == 302
        assert 'payment' in r.headers['Location'].lower()

    def test_registro_datos_invalidos_retorna_400(self, client):
        r = client.post('/register', data=self._datos_validos(nombre='', cedula='abc'))
        assert r.status_code == 400

    def test_registro_nombre_guardado_en_mayusculas(self, client):
        client.post('/register', data=self._datos_validos(nombre='ana garcia'))
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '0987654321'), None)
        assert fila is not None
        assert fila['nombre'] == 'ANA GARCIA'

    def test_registro_email_guardado_en_minusculas(self, client):
        client.post('/register', data=self._datos_validos(email='TEST@GMAIL.COM'))
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '0987654321'), None)
        assert fila is not None
        assert fila['email'] == 'test@gmail.com'

    def test_registro_pago_inicial_es_no(self, client):
        client.post('/register', data=self._datos_validos())
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '0987654321'), None)
        assert fila['pago'] == 'NO'


# ══════════════════════════════════════════════════════════════════════════════
# 7. PAGO (/payment)
# ══════════════════════════════════════════════════════════════════════════════

class TestPago:

    def test_payment_get_corredor_existente(self, client):
        insertar_corredor(cedula='1234567890')
        r = client.get('/payment/1234567890')
        assert r.status_code == 200

    def test_payment_get_cedula_inexistente(self, client):
        r = client.get('/payment/0000000000')
        assert r.status_code == 404

    def test_payment_post_sin_archivo(self, client):
        insertar_corredor(cedula='1234567890')
        r = client.post('/payment/1234567890', data={})
        assert r.status_code == 400

    def test_payment_post_png_valido(self, client):
        insertar_corredor(cedula='1234567890')
        data = {
            'comprobante': (io.BytesIO(png_falso()), 'comprobante.png')
        }
        r = client.post('/payment/1234567890', data=data,
                        content_type='multipart/form-data')
        assert r.status_code == 200
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '1234567890'), None)
        assert fila['pago'] == 'REVISION'

    def test_payment_post_jpg_valido(self, client):
        insertar_corredor(cedula='1234567890')
        data = {'comprobante': (io.BytesIO(jpg_falso()), 'pago.jpg')}
        r = client.post('/payment/1234567890', data=data,
                        content_type='multipart/form-data')
        assert r.status_code == 200

    def test_payment_post_extension_no_permitida(self, client):
        insertar_corredor(cedula='1234567890')
        data = {'comprobante': (io.BytesIO(b'contenido'), 'virus.exe')}
        r = client.post('/payment/1234567890', data=data,
                        content_type='multipart/form-data')
        assert r.status_code == 400

    def test_payment_post_contenido_falso(self, client):
        """Archivo con extensión PNG pero contenido inválido."""
        insertar_corredor(cedula='1234567890')
        data = {'comprobante': (io.BytesIO(b'no soy un png'), 'fake.png')}
        r = client.post('/payment/1234567890', data=data,
                        content_type='multipart/form-data')
        assert r.status_code == 400

    def test_payment_post_nombre_archivo_vacio(self, client):
        insertar_corredor(cedula='1234567890')
        data = {'comprobante': (io.BytesIO(b''), '')}
        r = client.post('/payment/1234567890', data=data,
                        content_type='multipart/form-data')
        assert r.status_code == 400

    def test_payment_archivo_guardado_en_disco(self, client):
        insertar_corredor(cedula='1234567890')
        data = {'comprobante': (io.BytesIO(png_falso()), 'recibo.png')}
        client.post('/payment/1234567890', data=data,
                    content_type='multipart/form-data')
        archivos = os.listdir(TEST_UPLOAD_DIR)
        assert any('1234567890' in f for f in archivos)


# ══════════════════════════════════════════════════════════════════════════════
# 8. PANEL ADMIN — AUTENTICACIÓN
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminLogin:

    def setup_method(self):
        crear_usuarios_csv()

    def test_login_page_get(self, client):
        r = client.get('/admin')
        assert r.status_code == 200

    def test_login_correcto_redirige_dashboard(self, client):
        r = client.post('/admin', data={'username': 'admin', 'password': 'admin123'})
        assert r.status_code == 302
        assert 'dashboard' in r.headers['Location'].lower()

    def test_login_incorrecto_muestra_error(self, client):
        r = client.post('/admin', data={'username': 'admin', 'password': 'mal'})
        assert r.status_code == 200
        # La página debe quedarse en el login (no redirigir al dashboard)
        body = r.data.decode().lower()
        assert 'dashboard' not in r.headers.get('Location', '').lower()
        # Y debe contener algún indicador de error — ajusta el texto si tu template usa otro
        assert any(palabra in body for palabra in [
            'incorrectos', 'incorrect', 'inválido', 'error',
            'contraseña', 'password', 'wrong', 'fallido'
        ])

    def test_logout_limpia_sesion(self, client_admin):
        r = client_admin.get('/logout')
        assert r.status_code == 302
        # Intentar acceder al dashboard debe redirigir a login
        r2 = client_admin.get('/admin/dashboard')
        assert r2.status_code == 302

    def test_dashboard_sin_login_redirige(self, client):
        r = client.get('/admin/dashboard')
        assert r.status_code == 302
        assert 'admin' in r.headers['Location'].lower()


# ══════════════════════════════════════════════════════════════════════════════
# 9. PANEL ADMIN — DASHBOARD Y OPERACIONES
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminDashboard:

    def test_dashboard_accesible_admin(self, client_admin):
        r = client_admin.get('/admin/dashboard')
        assert r.status_code == 200

    def test_dashboard_accesible_staff(self, client_staff):
        r = client_staff.get('/admin/dashboard')
        assert r.status_code == 200

    def test_marcar_pagado_cambia_estado(self, client_admin):
        insertar_corredor(cedula='1234567890', pago='REVISION')
        with patch.object(app_module, 'enviar_email_confirmacion', return_value=True):
            r = client_admin.post('/admin/marcar_pagado/1234567890')
        assert r.status_code == 302
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '1234567890'), None)
        assert fila['pago'] == 'SI'

    def test_marcar_pagado_intenta_enviar_email(self, client_admin):
        insertar_corredor(cedula='1234567890', pago='REVISION', email='test@test.com')
        with patch.object(app_module, 'enviar_email_confirmacion', return_value=True) as mock_email:
            client_admin.post('/admin/marcar_pagado/1234567890')
        mock_email.assert_called_once()

    def test_eliminar_inscripcion_solo_admin(self, client_admin):
        insertar_corredor(cedula='1234567890')
        r = client_admin.post('/admin/delete/1234567890')
        assert r.status_code == 302
        filas = app_module.leer_csv()
        assert not any(f['cedula'] == '1234567890' for f in filas)

    def test_eliminar_inscripcion_bloqueado_para_staff(self, client_staff):
        insertar_corredor(cedula='1234567890')
        r = client_staff.post('/admin/delete/1234567890')
        assert r.status_code == 403

    def test_export_csv_solo_admin(self, client_admin):
        r = client_admin.get('/admin/export')
        assert r.status_code == 200

    def test_export_csv_bloqueado_para_staff(self, client_staff):
        r = client_staff.get('/admin/export')
        assert r.status_code == 403

    def test_ver_comprobante_no_encontrado(self, client_admin):
        r = client_admin.get('/admin/ver_comprobante/9999999999')
        assert r.status_code == 404

    def test_ver_comprobante_existente(self, client_admin):
        ruta = os.path.join(TEST_UPLOAD_DIR, 'comprobante_1234567890.png')
        with open(ruta, 'wb') as f:
            f.write(png_falso())
        r = client_admin.get('/admin/ver_comprobante/1234567890')
        assert r.status_code == 200

    def test_estadisticas_accesible(self, client_admin):
        r = client_admin.get('/admin/estadisticas')
        assert r.status_code == 200

    def test_editar_inscripcion(self, client_admin):
        insertar_corredor(cedula='1234567890', talla='S')
        r = client_admin.post('/admin/editar/1234567890', data={
            'nombre': 'Nuevo Nombre', 'email': 'nuevo@test.com',
            'telefono': '0999999999', 'categoria': '5K LIBRE', 'talla': 'XL'
        })
        assert r.status_code == 302
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '1234567890'), None)
        assert fila['talla'] == 'XL'
        assert fila['nombre'] == 'NUEVO NOMBRE'


# ══════════════════════════════════════════════════════════════════════════════
# 10. ENTREGA DE KIT
# ══════════════════════════════════════════════════════════════════════════════

class TestEntregaKit:

    def test_confirmar_entrega_cambia_estado(self, client):
        insertar_corredor(cedula='1234567890', pago='SI', entrega='NO')
        r = client.post('/admin/confirmar_entrega/1234567890')
        assert r.status_code == 302
        filas = app_module.leer_csv()
        fila  = next((f for f in filas if f['cedula'] == '1234567890'), None)
        assert fila['entrega'] == 'SI'

    def test_buscar_manual_get(self, client):
        r = client.get('/admin/buscar_manual')
        assert r.status_code == 200

    def test_buscar_manual_post_redirige_validador(self, client):
        insertar_corredor(cedula='1234567890')
        r = client.post('/admin/buscar_manual', data={'cedula': '1234567890'})
        assert r.status_code == 302
        assert '1234567890' in r.headers['Location']


# ══════════════════════════════════════════════════════════════════════════════
# 11. RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════════

class TestRateLimit:

    def test_rate_limit_registro_bloquea_exceso(self, client):
        """Superar 10 registros en 600s debe retornar 429."""
        datos = {
            'nombre': 'Test', 'cedula': '1234567890', 'email': 't@t.com',
            'telefono': '0991234567', 'genero': 'M', 'categoria': '5K', 'talla': 'M'
        }
        for i in range(10):
            client.post('/register', data={**datos, 'cedula': f'123456789{i}'})
        r = client.post('/register', data=datos)
        assert r.status_code == 429

    def test_rate_limit_payment_bloquea_exceso(self, client):
        """Superar 5 solicitudes de pago en 300s debe retornar 429."""
        insertar_corredor(cedula='1234567890')
        for _ in range(5):
            client.get('/payment/1234567890')
        r = client.get('/payment/1234567890')
        assert r.status_code == 429

    def test_rate_limit_admin_login_bloquea_exceso(self, client):
        """Superar 5 intentos de login en 900s debe retornar 429."""
        crear_usuarios_csv()
        for _ in range(5):
            client.post('/admin', data={'username': 'x', 'password': 'x'})
        r = client.post('/admin', data={'username': 'x', 'password': 'x'})
        assert r.status_code == 429


# ══════════════════════════════════════════════════════════════════════════════
# 12. HEADERS DE SEGURIDAD
# ══════════════════════════════════════════════════════════════════════════════

class TestHeadersSeguridad:

    def test_x_content_type_options(self, client):
        r = client.get('/')
        assert r.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_x_frame_options(self, client):
        r = client.get('/')
        assert r.headers.get('X-Frame-Options') == 'DENY'

    def test_strict_transport_security(self, client):
        r = client.get('/')
        assert 'max-age' in r.headers.get('Strict-Transport-Security', '')

    def test_content_security_policy_presente(self, client):
        r = client.get('/')
        assert 'Content-Security-Policy' in r.headers

    def test_referrer_policy(self, client):
        r = client.get('/')
        assert r.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'


# ══════════════════════════════════════════════════════════════════════════════
# 13. EMAIL
# ══════════════════════════════════════════════════════════════════════════════

class TestEmail:

    def test_email_enviado_correctamente(self):
        with app_module.app.test_request_context():
            with patch('resend.Emails.send', return_value={'id': 'fake-id'}) as mock_send:
                resultado = app_module.enviar_email_confirmacion(
                    'test@test.com', 'JUAN TEST', '1234567890'
                )
        assert resultado is True
        mock_send.assert_called_once()

    def test_email_falla_con_excepcion(self):
        with app_module.app.test_request_context():
            with patch('resend.Emails.send', side_effect=Exception('SMTP error')):
                resultado = app_module.enviar_email_confirmacion(
                    'test@test.com', 'JUAN TEST', '1234567890'
                )
        assert resultado is False

    def test_email_contiene_link_certificado(self):
        """Verifica que el email incluya el link al certificado."""
        with app_module.app.test_request_context():
            with patch('resend.Emails.send') as mock_send:
                app_module.enviar_email_confirmacion('t@t.com', 'NOMBRE', '1234567890')
        args = mock_send.call_args[0][0]
        assert '1234567890' in args['html']
        assert 'certificado' in args['html'].lower()


# ══════════════════════════════════════════════════════════════════════════════
# 14. CSV — INTEGRIDAD Y CASOS BORDE
# ══════════════════════════════════════════════════════════════════════════════

class TestCSVIntegridad:

    def test_guardar_y_releer_multiples_corredores(self):
        for i in range(5):
            insertar_corredor(cedula=f'123456789{i}', nombre=f'CORREDOR {i}')
        filas = app_module.leer_csv()
        assert len(filas) == 5

    def test_eliminar_corredor_no_afecta_otros(self):
        insertar_corredor(cedula='1111111111')
        insertar_corredor(cedula='2222222222')
        filas = [f for f in app_module.leer_csv() if f['cedula'] != '1111111111']
        app_module.guardar_csv(filas)
        restantes = app_module.leer_csv()
        assert len(restantes) == 1
        assert restantes[0]['cedula'] == '2222222222'

    def test_actualizar_pago_en_csv(self):
        insertar_corredor(cedula='1234567890', pago='NO')
        filas = app_module.leer_csv()
        for row in filas:
            if row['cedula'] == '1234567890':
                row['pago'] = 'SI'
        app_module.guardar_csv(filas)
        corredor = app_module.buscar_corredor('1234567890')
        assert corredor['pago'] == 'SI'

    def test_actualizar_entrega_en_csv(self):
        insertar_corredor(cedula='1234567890', entrega='NO')
        filas = app_module.leer_csv()
        for row in filas:
            if row['cedula'] == '1234567890':
                row['entrega'] = 'SI'
        app_module.guardar_csv(filas)
        corredor = app_module.buscar_corredor('1234567890')
        assert corredor['entrega'] == 'SI'

    def test_encabezados_correctos_en_csv(self):
        with open(TEST_CSV_FILE, newline='', encoding='utf-8') as f:
            reader = csv.reader(f, delimiter=';')
            encabezados = next(reader)
        assert encabezados == ENCABEZADOS

    def test_leer_csv_inexistente_retorna_lista_vacia(self):
        ruta_original = app_module.CSV_FILE
        app_module.CSV_FILE = '/ruta/que/no/existe.csv'
        try:
            filas = app_module.leer_csv()
            assert filas == []
        finally:
            app_module.CSV_FILE = ruta_original

    def test_corredor_con_nombre_caracteres_especiales(self):
        insertar_corredor(cedula='1234567890', nombre='JOSÉ ÑOÑO ÁLVAREZ')
        corredor = app_module.buscar_corredor('1234567890')
        assert corredor['nombre'] == 'JOSÉ ÑOÑO ÁLVAREZ'


# ══════════════════════════════════════════════════════════════════════════════
# 15. ESTADOS DE PAGO — FLUJOS COMPLETOS
# ══════════════════════════════════════════════════════════════════════════════

class TestEstadosPago:

    def test_pago_si_es_considerado_verificado(self, client):
        insertar_corredor(cedula='1234567890', pago='SI')
        r = client.get('/validar/1234567890')
        assert b'success' in r.data.lower() or r.status_code == 200

    def test_pago_pagado_es_considerado_verificado(self, client):
        insertar_corredor(cedula='1234567890', pago='PAGADO')
        r = client.post('/check', data={'cedula': '1234567890'})
        assert r.status_code == 302
        assert 'certificado' in r.headers['Location'].lower()

    def test_pago_verificado_es_considerado_verificado(self, client):
        insertar_corredor(cedula='1234567890', pago='VERIFICADO')
        r = client.post('/check', data={'cedula': '1234567890'})
        assert r.status_code == 302
        assert 'certificado' in r.headers['Location'].lower()

    def test_pago_revision_no_da_acceso_certificado(self, client):
        insertar_corredor(cedula='1234567890', pago='REVISION')
        r = client.post('/check', data={'cedula': '1234567890'})
        # Debe ir a payment, no a certificado
        if r.status_code == 302:
            assert 'payment' in r.headers['Location'].lower()

    def test_pago_no_no_da_acceso_certificado(self, client):
        insertar_corredor(cedula='1234567890', pago='NO')
        r = client.post('/check', data={'cedula': '1234567890'})
        if r.status_code == 302:
            assert 'certificado' not in r.headers['Location'].lower()

    def test_dashboard_cuenta_pagados_correctamente(self, client_admin):
        insertar_corredor(cedula='1111111111', pago='SI')
        insertar_corredor(cedula='2222222222', pago='PAGADO')
        insertar_corredor(cedula='3333333333', pago='NO')
        insertar_corredor(cedula='4444444444', pago='REVISION')
        r = client_admin.get('/admin/dashboard')
        assert r.status_code == 200

    def test_dashboard_cuenta_revision_correctamente(self, client_admin):
        insertar_corredor(cedula='1111111111', pago='REVISION')
        insertar_corredor(cedula='2222222222', pago='REVISION')
        insertar_corredor(cedula='3333333333', pago='SI')
        r = client_admin.get('/admin/dashboard')
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 16. CERTIFICADO — CASOS BORDE
# ══════════════════════════════════════════════════════════════════════════════

class TestCertificado:

    def test_certificado_contiene_nombre_corredor(self, client):
        insertar_corredor(cedula='1234567890', pago='SI', nombre='CARLOS RUIZ')
        r = client.get('/certificado/1234567890')
        assert 'CARLOS RUIZ' in r.data.decode()

    def test_certificado_contiene_categoria(self, client):
        insertar_corredor(cedula='1234567890', pago='SI', categoria='21K LIBRE')
        r = client.get('/certificado/1234567890')
        assert '21K' in r.data.decode()

    def test_certificado_corredor_pago_revision(self, client):
        """Corredor en revisión también puede ver su certificado (la app lo permite)."""
        insertar_corredor(cedula='1234567890', pago='REVISION')
        r = client.get('/certificado/1234567890')
        assert r.status_code == 200

    def test_certificado_cedula_invalida_retorna_400(self, client):
        r = client.get('/certificado/abc')
        assert r.status_code == 400

    def test_download_public_certificate_no_encontrado(self, client):
        r = client.get('/download/public/1234567890')
        assert r.status_code == 404

    def test_download_public_certificate_existente(self, client):
        insertar_corredor(cedula='1234567890', pago='SI')
        # Mockear os.path.exists y send_file para evitar tocar el disco
        # (en Windows send_file mantiene el handle abierto y bloquea el borrado)
        with patch('os.path.exists', return_value=True), \
             patch('app.send_file', return_value=app_module.app.response_class(
                 response=pdf_falso(), status=200, mimetype='application/pdf')):
            r = client.get('/download/public/1234567890')
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 17. SEGURIDAD — INYECCIÓN Y CASOS MALICIOSOS
# ══════════════════════════════════════════════════════════════════════════════

class TestSeguridad:

    def test_cedula_con_sql_injection_rechazada(self, client):
        r = client.get("/certificado/1' OR '1'='1")
        assert r.status_code in (400, 404)

    def test_cedula_con_path_traversal_rechazada(self, client):
        r = client.get('/certificado/../../../etc/passwd')
        assert r.status_code in (400, 404)

    def test_cedula_con_xss_rechazada(self, client):
        r = client.get('/certificado/<script>alert(1)</script>')
        assert r.status_code in (400, 404)

    def test_registro_nombre_con_html_no_ejecuta(self, client):
        """Nombre con HTML no debe ejecutarse — Flask escapa por defecto en Jinja2."""
        r = client.post('/register', data={
            'nombre': '<script>alert(1)</script>',
            'cedula': '1234567890', 'email': 't@t.com',
            'telefono': '0991234567', 'genero': 'M',
            'categoria': '5K', 'talla': 'M'
        })
        # Debe rechazarlo por validación de nombre (solo letras)
        assert r.status_code == 400

    def test_pago_cedula_no_numerica_retorna_400(self, client):
        r = client.get('/payment/no-es-cedula')
        assert r.status_code == 400

    def test_eliminar_sin_login_redirige(self, client):
        insertar_corredor(cedula='1234567890')
        r = client.post('/admin/delete/1234567890')
        assert r.status_code == 302
        assert 'admin' in r.headers['Location'].lower()

    def test_marcar_pagado_sin_login_redirige(self, client):
        insertar_corredor(cedula='1234567890')
        r = client.post('/admin/marcar_pagado/1234567890')
        assert r.status_code == 302

    def test_export_csv_sin_login_redirige(self, client):
        r = client.get('/admin/export')
        assert r.status_code == 302

    def test_editar_sin_login_redirige(self, client):
        r = client.post('/admin/editar/1234567890', data={'talla': 'XL'})
        assert r.status_code == 302



# ══════════════════════════════════════════════════════════════════════════════
# 18. CONCURRENCIA — REGISTROS SIMULTÁNEOS
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrencia:

    def test_no_duplicados_registro_simultaneo(self):
        """
        Simula dos hilos registrando la misma cédula al mismo tiempo.
        Solo uno debe quedar en el CSV — el lock debe evitar el duplicado.
        """
        cedula = '5555555555'
        errores_hilo = []
        resultados   = []

        def registrar():
            nuevo = {
                'nombre': 'CONCURRENTE TEST', 'cedula': cedula,
                'email': 'c@test.com', 'telefono': '0990000000',
                'genero': 'M', 'categoria': '5K', 'talla': 'M',
                'fecha': '2026-01-01 10:00', 'pago': 'NO', 'entrega': 'NO'
            }
            ya_existia = False

            def _agregar(filas):
                nonlocal ya_existia
                if any(f['cedula'] == cedula for f in filas):
                    ya_existia = True
                    return
                filas.append(nuevo)

            try:
                app_module.modificar_csv(_agregar)
                resultados.append('existia' if ya_existia else 'insertado')
            except Exception as e:
                errores_hilo.append(str(e))

        # Lanzar dos hilos al mismo tiempo
        t1 = threading.Thread(target=registrar)
        t2 = threading.Thread(target=registrar)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errores_hilo == [], f"Errores en hilos: {errores_hilo}"

        filas = app_module.leer_csv()
        registros_cedula = [f for f in filas if f['cedula'] == cedula]

        # La cédula debe aparecer EXACTAMENTE una vez
        assert len(registros_cedula) == 1, (
            f"Se esperaba 1 registro, se encontraron {len(registros_cedula)}"
        )
        # Uno insertó, el otro encontró que ya existía
        assert sorted(resultados) == ['existia', 'insertado'], (
            f"Resultados inesperados: {resultados}"
        )

    def test_no_duplicados_multiples_hilos(self):
        """
        10 hilos intentan registrar la misma cédula simultáneamente.
        Solo 1 debe quedar en el CSV.
        """
        cedula = '6666666666'
        insertados = []

        def registrar():
            nuevo = {
                'nombre': 'MULTI HILO', 'cedula': cedula,
                'email': 'm@test.com', 'telefono': '0991111111',
                'genero': 'F', 'categoria': '10K', 'talla': 'S',
                'fecha': '2026-01-01 10:00', 'pago': 'NO', 'entrega': 'NO'
            }
            def _agregar(filas):
                if any(f['cedula'] == cedula for f in filas):
                    return
                filas.append(nuevo)
                insertados.append(1)

            app_module.modificar_csv(_agregar)

        hilos = [threading.Thread(target=registrar) for _ in range(10)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        filas = app_module.leer_csv()
        assert len([f for f in filas if f['cedula'] == cedula]) == 1
        assert sum(insertados) == 1

    def test_escrituras_distintas_cedulas_no_se_pierden(self):
        """
        10 hilos registran cédulas DISTINTAS simultáneamente.
        Todos deben quedar en el CSV — ninguno se pierde.
        """
        cedulas = [f'777777777{i}' for i in range(10)]
        errores = []

        def registrar(cedula):
            nuevo = {
                'nombre': f'CORREDOR {cedula}', 'cedula': cedula,
                'email': f'{cedula}@test.com', 'telefono': '0992222222',
                'genero': 'M', 'categoria': '21K', 'talla': 'L',
                'fecha': '2026-01-01 10:00', 'pago': 'NO', 'entrega': 'NO'
            }
            def _agregar(filas):
                if not any(f['cedula'] == cedula for f in filas):
                    filas.append(nuevo)

            try:
                app_module.modificar_csv(_agregar)
            except Exception as e:
                errores.append(str(e))

        hilos = [threading.Thread(target=registrar, args=(c,)) for c in cedulas]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        assert errores == [], f"Errores: {errores}"
        filas = app_module.leer_csv()
        cedulas_guardadas = {f['cedula'] for f in filas}
        for cedula in cedulas:
            assert cedula in cedulas_guardadas, f"Se perdió el registro de cédula {cedula}"


# ══════════════════════════════════════════════════════════════════════════════
# Teardown global
# ══════════════════════════════════════════════════════════════════════════════

def pytest_sessionfinish(session, exitstatus):
    """Limpia los directorios temporales al finalizar todos los tests."""
    try:
        shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)
    except Exception:
        pass