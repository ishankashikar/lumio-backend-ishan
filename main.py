from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from core.exceptions import LumioException, lumio_exception_handler, generic_exception_handler
from api import connection, procedures, report, export, ai
from utils.file_utils import ensure_dirs
import oracledb

oracledb.init_oracle_client(lib_dir=r"D:\instantclient_21_20")

app = FastAPI(title="Lumio Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(LumioException, lumio_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

ensure_dirs()

app.include_router(connection.router, prefix="/connect",    tags=["Connection"])
app.include_router(procedures.router, prefix="/procedures", tags=["Procedures"])
app.include_router(report.router,     prefix="/report",     tags=["Report"])
app.include_router(export.router,     prefix="/export",     tags=["Export"])
app.include_router(ai.router,         prefix="/ai",         tags=["AI"])