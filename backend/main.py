"""API de la Kata App : une liste d'items stockée dans PostgreSQL."""
import logging
import os
import socket
import threading
from contextlib import asynccontextmanager, contextmanager

# L'observabilité doit être activée AVANT d'importer FastAPI :
# elle remplace la classe FastAPI par une version instrumentée.
# Sans chaîne de connexion (en local, en test), on s'en passe.
if os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(
        logger_name="api",
        sampling_ratio=float(os.getenv("TRACE_SAMPLING_RATIO", "1.0")),
    )

import psycopg2  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from psycopg2 import pool  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

# --- Base de données -------------------------------------------------------

POOL_MAX = int(os.getenv("DB_POOL_MAX", "5"))
DB_PARAMS = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "app"),
    "user": os.getenv("DB_USER", "app"),
    "password": os.getenv("DB_PASSWORD", "app"),
    "sslmode": os.getenv("DB_SSLMODE", "prefer"),
    "connect_timeout": 3,  # une base injoignable répond en 3 s, pas en 2 min
    # Connexion déjà ouverte vers une base qui disparaît : sans ces réglages,
    # une requête attend indéfiniment une réponse qui ne viendra jamais.
    "keepalives": 1,
    "keepalives_idle": 5,
    "keepalives_interval": 2,
    "keepalives_count": 2,
    "tcp_user_timeout": 5000,
}

# Pool créé au premier besoin : l'API démarre même sans base.
# minconn=POOL_MAX : psycopg2 garde les connexions ouvertes et les réutilise
# (avec minconn=0, il fermait chaque connexion rendue : une nouvelle connexion TLS par requête).
_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = pool.ThreadedConnectionPool(POOL_MAX, POOL_MAX, **DB_PARAMS)
        return _pool
# Le pool de psycopg2 échoue tout de suite quand il est plein.
# Ce sémaphore fait patienter les requêtes au plus 2 s à la place.
_slots = threading.BoundedSemaphore(POOL_MAX)


class DatabaseUnavailable(Exception):
    """La base ne répond pas : l'API renvoie une 503 propre."""


@contextmanager
def get_conn():
    if not _slots.acquire(timeout=2):
        raise DatabaseUnavailable("pool de connexions saturé")
    conn, broken = None, False
    try:
        conn = _get_pool().getconn()
        with conn:  # commit si tout va bien, rollback sinon
            yield conn
    except (psycopg2.OperationalError, psycopg2.InterfaceError) as exc:
        broken = True  # connexion cassée : on la jette au lieu de la réutiliser
        raise DatabaseUnavailable(str(exc).strip()) from exc
    finally:
        if conn is not None:
            _get_pool().putconn(conn, close=broken or bool(conn.closed))
        _slots.release()


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
_schema_ready = False


def ensure_schema():
    """Crée la table au premier accès réussi à la base."""
    global _schema_ready
    if _schema_ready:
        return
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA)
    _schema_ready = True


# --- API -------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app):
    try:
        ensure_schema()
    except DatabaseUnavailable as exc:
        log.warning("base indisponible au démarrage, nouvel essai au premier appel : %s", exc)
    yield
if _pool is not None:
        _pool.closeall()


app = FastAPI(title="kata-api", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("WEB_ORIGIN", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(DatabaseUnavailable)
def db_unavailable(request: Request, exc: DatabaseUnavailable):
    log.error("base indisponible : %s", exc)
    return JSONResponse(status_code=503, content={"detail": "database unavailable"})


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.get("/health")
def health():
    """Le processus répond. Ne teste PAS la base (voir README, décisions)."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Test complet, base comprise : utilisé par le smoke test."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
    return {"status": "ready"}


@app.get("/api/info")
def info():
    # hostname = nom de la réplica : on le voit changer quand l'app scale.
    return {"hostname": socket.gethostname(), "version": os.getenv("APP_VERSION", "dev")}


@app.get("/api/items")
def list_items():
    ensure_schema()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name FROM items ORDER BY id DESC LIMIT 20")
        rows = cur.fetchall()
    return [{"id": r[0], "name": r[1]} for r in rows]


@app.post("/api/items", status_code=201)
def create_item(item: ItemIn):
    ensure_schema()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO items (name) VALUES (%s) RETURNING id", (item.name,))
        new_id = cur.fetchone()[0]
    log.info("item créé id=%s", new_id)
    return {"id": new_id, "name": item.name}


@app.post("/api/crash")
def crash():
    """Démo de panne : arrête brutalement la réplica. Désactivé par défaut."""
    if os.getenv("CHAOS_ENABLED") != "true":
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    log.warning("crash volontaire de la réplica %s dans 1 s", socket.gethostname())
    # On répond AVANT de mourir : sinon l'ingress voit la connexion coupée,
    # rejoue la requête sur une autre réplica, qui meurt à son tour.
    threading.Timer(1.0, os._exit, args=(1,)).start()
    return JSONResponse(
        status_code=202,
        content={"detail": "crash dans 1 s", "hostname": socket.gethostname()},
    )