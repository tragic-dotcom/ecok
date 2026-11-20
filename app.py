import os
from datetime import datetime, timezone
from urllib.parse import quote

from bson import ObjectId
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from pymongo import ASCENDING, MongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi
from werkzeug.middleware.proxy_fix import ProxyFix


load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "").strip()
MONGO_DB = os.getenv("MONGO_DB", "catalogo_qr").strip() or "catalogo_qr"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "clave-demostracion-qr")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

ESTADOS = ("Disponible", "Ultimas unidades", "Agotado")
cliente_mongo = None
datos_iniciales_revisados = False


class ConfiguracionMongoError(RuntimeError):
    """Indica que falta la cadena de conexión de MongoDB Atlas."""


def obtener_cliente():
    global cliente_mongo

    if not MONGO_URI:
        raise ConfiguracionMongoError("Falta configurar MONGO_URI.")

    if cliente_mongo is None:
        cliente_mongo = MongoClient(
            MONGO_URI,
            server_api=ServerApi("1"),
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            tz_aware=True,
        )

    return cliente_mongo


def obtener_bd():
    return obtener_cliente()[MONGO_DB]


def obtener_productos():
    return obtener_bd()["productos"]


def asegurar_datos_iniciales():
    global datos_iniciales_revisados

    if datos_iniciales_revisados:
        return

    coleccion = obtener_productos()
    coleccion.create_index([("codigo", ASCENDING)], unique=True)

    if coleccion.count_documents({}) == 0:
        ahora = datetime.now(timezone.utc)
        coleccion.insert_many(
            [
                {
                    "codigo": "CAFE-001",
                    "nombre": "Café caramelo",
                    "categoria": "Bebida",
                    "descripcion": "Café frío con leche, caramelo y crema ligera.",
                    "ubicacion": "Barra principal",
                    "estado": "Disponible",
                    "icono": "CAFE",
                    "actualizado": ahora,
                },
                {
                    "codigo": "PAN-001",
                    "nombre": "Pan de chocolate",
                    "categoria": "Postre",
                    "descripcion": "Pan suave relleno de chocolate, preparado el mismo día.",
                    "ubicacion": "Vitrina de postres",
                    "estado": "Ultimas unidades",
                    "icono": "PAN",
                    "actualizado": ahora,
                },
                {
                    "codigo": "TE-001",
                    "nombre": "Té de frutos rojos",
                    "categoria": "Bebida",
                    "descripcion": "Infusión que puede pedirse caliente o fría.",
                    "ubicacion": "Barra principal",
                    "estado": "Disponible",
                    "icono": "TE",
                    "actualizado": ahora,
                },
                {
                    "codigo": "COMBO-001",
                    "nombre": "Combo estudiantil",
                    "categoria": "Promoción",
                    "descripcion": "Bebida del día y pan dulce a precio especial.",
                    "ubicacion": "Mostrador",
                    "estado": "Agotado",
                    "icono": "COMBO",
                    "actualizado": ahora,
                },
            ]
        )

    datos_iniciales_revisados = True


def convertir_id(producto_id):
    if not ObjectId.is_valid(producto_id):
        abort(404)
    return ObjectId(producto_id)


def preparar_producto(documento):
    producto = dict(documento)
    producto["id"] = str(producto.pop("_id"))

    fecha = producto.get("actualizado")
    if isinstance(fecha, datetime):
        producto["actualizado"] = fecha.astimezone().strftime("%d/%m/%Y %H:%M")
    else:
        producto["actualizado"] = str(fecha or "Sin fecha")

    return producto


def buscar_producto(producto_id):
    objeto_id = convertir_id(producto_id)
    documento = obtener_productos().find_one({"_id": objeto_id})
    if documento is None:
        abort(404)
    return preparar_producto(documento)


def enlace_publico(producto_id):
    ruta = url_for("ficha_publica", producto_id=producto_id)
    base_configurada = os.getenv("APP_BASE_URL", "").strip().rstrip("/")

    if base_configurada:
        return f"{base_configurada}{ruta}"

    return url_for("ficha_publica", producto_id=producto_id, _external=True)


def enlace_qr(destino):
    destino_codificado = quote(destino, safe="")
    return (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size=260x260&margin=12&data={destino_codificado}"
    )


@app.route("/")
def inicio():
    asegurar_datos_iniciales()
    documentos = obtener_productos().find().sort("nombre", ASCENDING)
    productos = []

    for documento in documentos:
        producto = preparar_producto(documento)
        producto["enlace_publico"] = enlace_publico(producto["id"])
        producto["enlace_qr"] = enlace_qr(producto["enlace_publico"])
        productos.append(producto)

    return render_template(
        "index.html",
        productos=productos,
        estados=ESTADOS,
        nombre_bd=MONGO_DB,
    )


@app.route("/ficha/<producto_id>")
def ficha_publica(producto_id):
    producto = buscar_producto(producto_id)
    return render_template("ficha_publica.html", producto=producto)


@app.route("/producto/<producto_id>/estado", methods=["POST"])
def cambiar_estado(producto_id):
    producto = buscar_producto(producto_id)
    estado = request.form.get("estado", "")

    if estado not in ESTADOS:
        flash("Selecciona un estado válido.", "error")
        return redirect(url_for("inicio"))

    obtener_productos().update_one(
        {"_id": convertir_id(producto_id)},
        {
            "$set": {
                "estado": estado,
                "actualizado": datetime.now(timezone.utc),
            }
        },
    )

    flash(
        f"El estado de {producto['nombre']} cambió a {estado}.",
        "exito",
    )
    return redirect(url_for("inicio", actualizado=producto_id))


@app.route("/producto/nuevo", methods=["GET", "POST"])
def nuevo_producto():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        categoria = request.form.get("categoria", "").strip()
        descripcion = request.form.get("descripcion", "").strip()
        ubicacion = request.form.get("ubicacion", "").strip()
        icono = request.form.get("icono", "ITEM").strip().upper()[:8] or "ITEM"

        if not all((nombre, categoria, descripcion, ubicacion)):
            flash("Completa todos los campos.", "error")
            return render_template("nuevo_producto.html")

        obtener_productos().insert_one(
            {
                "codigo": f"ITEM-{ObjectId()}",
                "nombre": nombre,
                "categoria": categoria,
                "descripcion": descripcion,
                "ubicacion": ubicacion,
                "estado": "Disponible",
                "icono": icono,
                "actualizado": datetime.now(timezone.utc),
            }
        )

        flash("Registro creado en MongoDB Atlas. Su código QR ya está disponible.", "exito")
        return redirect(url_for("inicio"))

    return render_template("nuevo_producto.html")


@app.route("/salud")
def salud():
    try:
        obtener_cliente().admin.command("ping")
        return jsonify(estado="ok", base=MONGO_DB)
    except (ConfiguracionMongoError, PyMongoError):
        return jsonify(estado="sin_conexion"), 503


@app.errorhandler(ConfiguracionMongoError)
def falta_configuracion(_error):
    return render_template("configuracion_mongo.html"), 503


@app.errorhandler(PyMongoError)
def error_de_mongo(_error):
    return render_template("error_mongo.html"), 503


@app.errorhandler(404)
def no_encontrado(_error):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "1") == "1",
    )
