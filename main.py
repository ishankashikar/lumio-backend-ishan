import oracledb
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.exceptions import (
    LumioException,
    lumio_exception_handler,
    generic_exception_handler,
)
from api import connection, procedures, report, export, ai
from utils.file_utils import (
    ensure_dirs,
    clean_old_exports,
    clean_old_report_outputs,
)
from services.cache_service import ping as cache_ping

# Oracle thick mode — must be before any DB calls
oracledb.init_oracle_client(lib_dir=r"D:\instantclient_21_20")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    ensure_dirs()
    clean_old_exports(days=7)
    clean_old_report_outputs(days=30)
    await cache_ping()
    yield
    # Shutdown — add cleanup here if needed later


app = FastAPI(
    title    = "Lumio Backend",
    version  = "1.0.0",
    lifespan = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins  = ["*"],
    allow_methods  = ["*"],
    allow_headers  = ["*"],
)

app.add_exception_handler(LumioException, lumio_exception_handler)
app.add_exception_handler(Exception,      generic_exception_handler)

app.include_router(connection.router, prefix="/connect",    tags=["Connection"])
app.include_router(procedures.router, prefix="/procedures", tags=["Procedures"])
app.include_router(report.router,     prefix="/report",     tags=["Report"])
app.include_router(export.router,     prefix="/export",     tags=["Export"])
app.include_router(ai.router,         prefix="/ai",         tags=["AI"])