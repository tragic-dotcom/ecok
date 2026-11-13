"""EcoBlack entry point.

- Local development: SQLite backend from app_sqlite.py when MONGODB_URI is absent.
- Render/production: requires MongoDB Atlas via MONGODB_URI and uses app_mongo.py.
"""
import os

IS_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"))
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()

if MONGODB_URI:
    from app_mongo import app
elif IS_RENDER:
    raise RuntimeError("MONGODB_URI es obligatoria en Render. Configura la cadena de MongoDB Atlas en Environment.")
else:
    from app_sqlite import app

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0" if IS_RENDER else "127.0.0.1", port=port, debug=not IS_RENDER)
