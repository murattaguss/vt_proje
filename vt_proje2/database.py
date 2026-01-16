"""
Database connection module for ToolShare application.

This module provides PostgreSQL database connection using SQLAlchemy
and raw SQL execution capabilities for stored procedures and triggers.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError
from contextlib import contextmanager
import os

# Database configuration
# Database configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5433/toolshare"
)

# SQLAlchemy engine and session setup
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """
    Dependency function to get database session.
    
    Yields:
        Session: SQLAlchemy database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_connection():
    """
    Context manager for database connections.
    
    Yields:
        Connection: Raw database connection for executing SQL.
    """
    connection = engine.connect()
    try:
        yield connection
    finally:
        connection.close()


def execute_raw_sql(query: str, params: dict = None):
    """
    Execute raw SQL query and return results.
    
    Args:
        query: SQL query string.
        params: Optional dictionary of parameters.
        
    Returns:
        list: Query results as list of dictionaries.
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
    Execute a PostgreSQL function and return results.
    
    Args:
        function_name: Name of the function to execute.
        *args: Arguments to pass to the function.
        
    Returns:
        list: Function results.
    """
    params_placeholder = ", ".join([f":p{i}" for i in range(len(args))])
    params = {f"p{i}": arg for i, arg in enumerate(args)}
    
    query = f"SELECT * FROM {function_name}({params_placeholder})"
    return execute_raw_sql(query, params)


def call_procedure(procedure_name: str, *args):
    """
    Call a PostgreSQL stored procedure.
    
    Args:
        procedure_name: Name of the procedure to call.
        *args: Arguments to pass to the procedure.
    """
    params_placeholder = ", ".join([f":p{i}" for i in range(len(args))])
    params = {f"p{i}": arg for i, arg in enumerate(args)}
    
    query = f"CALL {procedure_name}({params_placeholder})"
    execute_raw_sql(query, params)
