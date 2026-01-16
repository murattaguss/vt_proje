# ToolShare uygulaması için veritabanı bağlantı modülü

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager
import os

# Veritabanı ayarlanması
DATABASE_URL = os.getenv(
    # Kendi serverinize göre değiştirmeyi unutmayın! Şifre ve portu kendi serverinize göre değiştirin
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5433/toolshare"
)

# SQLAlchemy engine ve session ayarlanması
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """
       Veritabanı oturumunu (session) almak için bağımlılık fonksiyonu

    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_connection():
    """
    Veritabanı bağlantıları için context manager (bağlam yöneticisi).

    """
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()


def execute_raw_sql(query: str, params: dict = None):
    """
    Ham SQL sorgusunu çalıştırır ve sonuçları döndürür.    
    Returns:
        list: Sözlük listesi olarak sorgu sonuçları.
    """
    with get_db_connection() as conn:
        try:
            result = conn.execute(text(query), params or {})
            if result.returns_rows:
                columns = result.keys()
                return [dict(zip(columns, row)) for row in result.fetchall()]
            conn.commit()
            return []
        except SQLAlchemyError as e:
            conn.rollback()
            raise e


def execute_function(function_name: str, *args):
    """
    Bir PostgreSQL fonksiyonunu çalıştırır ve sonuçları döndürür.
    Returns:
        list: Fonksiyon sonuçları.
    """
    params_placeholder = ", ".join([f":p{i}" for i in range(len(args))])
    params = {f"p{i}": arg for i, arg in enumerate(args)}
    
    query = f"SELECT * FROM {function_name}({params_placeholder})"
    return execute_raw_sql(query, params)


def call_procedure(procedure_name: str, *args):
    """
    Bir PostgreSQL saklı yordamını (stored procedure) çağırır.
    """
    params_placeholder = ", ".join([f":p{i}" for i in range(len(args))])
    params = {f"p{i}": arg for i, arg in enumerate(args)}
    
    query = f"CALL {procedure_name}({params_placeholder})"
    execute_raw_sql(query, params)
