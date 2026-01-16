"""
ToolShare - Neighborhood Tool & Equipment Sharing Platform

Main FastAPI application with all routes for user authentication,
tool management, reservations, and ratings.
"""

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from typing import Optional
from datetime import date
import os

from database import get_db, execute_raw_sql, execute_function

# Initialize FastAPI application
app = FastAPI(
    title="ToolShare",
    description="Neighborhood Tool & Equipment Sharing Platform",
    version="1.0.0"
)

# Template configuration
templates = Jinja2Templates(directory="templates")

# Simple session storage (in production, use proper session management)
user_sessions = {}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_current_user(request: Request) -> Optional[dict]:
    """
    Get current logged-in user from session.
    
    Args:
        request: FastAPI request object.
        
    Returns:
        dict: User data if logged in, None otherwise.
    """
    session_id = request.cookies.get("session_id")
    if session_id and session_id in user_sessions:
        return user_sessions[session_id]
    return None


def require_login(request: Request) -> dict:
    """
    Require user to be logged in.
    
    Args:
        request: FastAPI request object.
        
    Returns:
        dict: User data.
        
    Raises:
        HTTPException: If user is not logged in.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Please login first")
    return user


# =============================================================================
# AUTHENTICATION ROUTES
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the login/register page."""
    user = get_current_user(request)
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user, "message": message, "error": error}
    )


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Handle user login.
    
    Args:
        username: User's username.
        password: User's password.
        db: Database session.
        
    Returns:
        RedirectResponse: Redirect to dashboard on success.
    """
    try:
        result = db.execute(
            text("SELECT user_id, username, role, trust_score FROM users WHERE username = :username"),
            {"username": username}
        ).fetchone()
        
        if result:
            import uuid
            session_id = str(uuid.uuid4())
            user_sessions[session_id] = {
                "user_id": result[0],
                "username": result[1],
                "role": result[2],
                "trust_score": float(result[3]) if result[3] else 0.0
            }
            response = RedirectResponse(url="/dashboard", status_code=303)
            response.set_cookie("session_id", session_id)
            return response
        else:
            return RedirectResponse(url="/?error=Invalid username or password", status_code=303)
    except SQLAlchemyError as e:
        return RedirectResponse(url=f"/?error=Database error: {str(e)}", status_code=303)


@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Handle user registration.
    
    Args:
        username: Desired username.
        email: User's email.
        password: User's password.
        db: Database session.
        
    Returns:
        RedirectResponse: Redirect to login on success.
    """
    try:
        db.execute(
            text("""
                INSERT INTO users (username, email, password_hash, role)
                VALUES (:username, :email, :password, 'user')
            """),
            {"username": username, "email": email, "password": password}
        )
        db.commit()
        return RedirectResponse(url="/?message=Registration successful! Please login.", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = str(e)
        if "unique" in error_msg.lower():
            return RedirectResponse(url="/?error=Username or email already exists", status_code=303)
        return RedirectResponse(url=f"/?error=Registration failed: {error_msg}", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    """Handle user logout."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_id")
    return response


# =============================================================================
# DASHBOARD ROUTES
# =============================================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """
    Render user dashboard with all features.
    
    Shows tools, reservations, ratings, and admin features if applicable.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    
    try:
        # Get available tools using the VIEW
        available_tools = db.execute(
            text("SELECT * FROM v_available_tools")
        ).fetchall()
        
        # Get user's own tools
        my_tools = db.execute(
            text("SELECT * FROM tools WHERE owner_id = :user_id"),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # Get user's reservations (as borrower)
        my_reservations = db.execute(
            text("""
                SELECT r.*, t.name as tool_name, u.username as owner_name
                FROM reservations r
                JOIN tools t ON r.tool_id = t.tool_id
                JOIN users u ON t.owner_id = u.user_id
                WHERE r.borrower_id = :user_id
                ORDER BY r.start_date DESC
            """),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # Get reservations for user's tools (as lender)
        tool_reservations = db.execute(
            text("""
                SELECT r.*, t.name as tool_name, u.username as borrower_name
                FROM reservations r
                JOIN tools t ON r.tool_id = t.tool_id
                JOIN users u ON r.borrower_id = u.user_id
                WHERE t.owner_id = :user_id
                ORDER BY r.start_date DESC
            """),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # Get ratings received by user
        my_ratings = db.execute(
            text("""
                SELECT r.*, u.username as rater_name
                FROM ratings r
                JOIN users u ON r.rater_id = u.user_id
                WHERE r.rated_user_id = :user_id
                ORDER BY r.created_at DESC
            """),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # Get user activity report using FUNCTION with CURSOR
        activity_report = db.execute(
            text("SELECT * FROM get_user_activity_report(:user_id)"),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # AGGREGATE with HAVING: Users with average rating > 4.0
        top_rated_users = db.execute(
            text("""
                SELECT u.user_id, u.username, AVG(r.score) as avg_rating, COUNT(r.rating_id) as rating_count
                FROM users u
                JOIN ratings r ON u.user_id = r.rated_user_id
                GROUP BY u.user_id, u.username
                HAVING AVG(r.score) > 4.0
                ORDER BY avg_rating DESC
            """)
        ).fetchall()
        
        # SET OPERATION: Tools that have NEVER been reserved (using EXCEPT)
        never_reserved_tools = db.execute(
            text("""
                SELECT tool_id, name, category FROM tools
                EXCEPT
                SELECT DISTINCT t.tool_id, t.name, t.category 
                FROM tools t
                JOIN reservations r ON t.tool_id = r.tool_id
            """)
        ).fetchall()
        
        # Admin-only data
        all_users = []
        all_tools = []
        if user["role"] == "admin":
            all_users = db.execute(
                text("SELECT * FROM users ORDER BY user_id")
            ).fetchall()
            all_tools = db.execute(
                text("""
                    SELECT t.*, u.username as owner_name 
                    FROM tools t 
                    JOIN users u ON t.owner_id = u.user_id 
                    ORDER BY t.tool_id
                """)
            ).fetchall()
        
        # Get all tools for reservation dropdown
        all_available_tools = db.execute(
            text("SELECT tool_id, name FROM tools WHERE status = 'available'")
        ).fetchall()
        
        # Get completed reservations for rating (where user hasn't rated yet)
        ratable_reservations = db.execute(
            text("""
                SELECT r.reservation_id, t.name as tool_name, 
                       CASE WHEN r.borrower_id = :user_id THEN t.owner_id ELSE r.borrower_id END as other_user_id,
                       CASE WHEN r.borrower_id = :user_id THEN owner.username ELSE borrower.username END as other_user_name
                FROM reservations r
                JOIN tools t ON r.tool_id = t.tool_id
                JOIN users owner ON t.owner_id = owner.user_id
                JOIN users borrower ON r.borrower_id = borrower.user_id
                WHERE r.status = 'completed'
                  AND (r.borrower_id = :user_id OR t.owner_id = :user_id)
                  AND NOT EXISTS (
                      SELECT 1 FROM ratings rt 
                      WHERE rt.reservation_id = r.reservation_id 
                        AND rt.rater_id = :user_id
                  )
            """),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "message": message,
                "error": error,
                "available_tools": available_tools,
                "my_tools": my_tools,
                "my_reservations": my_reservations,
                "tool_reservations": tool_reservations,
                "my_ratings": my_ratings,
                "activity_report": activity_report,
                "top_rated_users": top_rated_users,
                "never_reserved_tools": never_reserved_tools,
                "all_users": all_users,
                "all_tools": all_tools,
                "all_available_tools": all_available_tools,
                "ratable_reservations": ratable_reservations
            }
        )
    except SQLAlchemyError as e:
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "error": f"Database error: {str(e)}",
                "message": "",
                "available_tools": [],
                "my_tools": [],
                "my_reservations": [],
                "tool_reservations": [],
                "my_ratings": [],
                "activity_report": [],
                "top_rated_users": [],
                "never_reserved_tools": [],
                "all_users": [],
                "all_tools": [],
                "all_available_tools": [],
                "ratable_reservations": []
            }
        )


# =============================================================================
# TOOL ROUTES
# =============================================================================

@app.post("/tools/add")
async def add_tool(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Add a new tool listing.
    
    Uses the tool_seq SEQUENCE for ID generation.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        # INSERT using the SEQUENCE (nextval is default in table definition)
        db.execute(
            text("""
                INSERT INTO tools (owner_id, name, description, category)
                VALUES (:owner_id, :name, :description, :category)
            """),
            {
                "owner_id": user["user_id"],
                "name": name,
                "description": description,
                "category": category
            }
        )
        db.commit()
        return RedirectResponse(url="/dashboard?message=Tool added successfully!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Failed to add tool: {str(e)}", status_code=303)


@app.post("/tools/delete/{tool_id}")
async def delete_tool(
    request: Request,
    tool_id: int,
    db: Session = Depends(get_db)
):
    """Delete a tool listing."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        # Check ownership
        result = db.execute(
            text("SELECT owner_id FROM tools WHERE tool_id = :tool_id"),
            {"tool_id": tool_id}
        ).fetchone()
        
        if not result:
            return RedirectResponse(url="/dashboard?error=Tool not found", status_code=303)
        
        if result[0] != user["user_id"] and user["role"] != "admin":
            return RedirectResponse(url="/dashboard?error=You can only delete your own tools", status_code=303)
        
        db.execute(
            text("DELETE FROM tools WHERE tool_id = :tool_id"),
            {"tool_id": tool_id}
        )
        db.commit()
        return RedirectResponse(url="/dashboard?message=Tool deleted successfully!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Failed to delete tool: {str(e)}", status_code=303)


@app.post("/tools/update/{tool_id}")
async def update_tool(
    request: Request,
    tool_id: int,
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    status: str = Form("available"),
    db: Session = Depends(get_db)
):
    """
    Update a tool listing.
    
    This will trigger trg_update_timestamp to update last_updated.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        db.execute(
            text("""
                UPDATE tools 
                SET name = :name, description = :description, 
                    category = :category, status = :status
                WHERE tool_id = :tool_id AND (owner_id = :owner_id OR :is_admin = true)
            """),
            {
                "tool_id": tool_id,
                "name": name,
                "description": description,
                "category": category,
                "status": status,
                "owner_id": user["user_id"],
                "is_admin": user["role"] == "admin"
            }
        )
        db.commit()
        return RedirectResponse(
            url="/dashboard?message=Tool updated! (Trigger updated timestamp)",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Failed to update tool: {str(e)}", status_code=303)


@app.get("/tools/search", response_class=HTMLResponse)
async def search_tools(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db)
):
    """
    Search tools by name.
    
    Uses the idx_tool_name INDEX for optimized searching.
    """
    user = get_current_user(request)
    
    try:
        # Search using the indexed column
        results = db.execute(
            text("""
                SELECT t.*, u.username as owner_name
                FROM tools t
                JOIN users u ON t.owner_id = u.user_id
                WHERE t.name ILIKE :query OR t.category ILIKE :query
                ORDER BY t.name
            """),
            {"query": f"%{q}%"}
        ).fetchall()
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user": user,
                "message": f"Found {len(results)} tools matching '{q}'",
                "error": "",
                "search_results": results,
                "search_query": q,
                "available_tools": [],
                "my_tools": [],
                "my_reservations": [],
                "tool_reservations": [],
                "my_ratings": [],
                "activity_report": [],
                "top_rated_users": [],
                "never_reserved_tools": [],
                "all_users": [],
                "all_tools": [],
                "all_available_tools": [],
                "ratable_reservations": []
            }
        )
    except SQLAlchemyError as e:
        return RedirectResponse(url=f"/dashboard?error=Search failed: {str(e)}", status_code=303)


# =============================================================================
# RESERVATION ROUTES
# =============================================================================

@app.post("/reservations/add")
async def add_reservation(
    request: Request,
    tool_id: int = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Create a new reservation.
    
    The trg_prevent_double_booking trigger will check for conflicts
    and raise an exception if the tool is already booked.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        db.execute(
            text("""
                INSERT INTO reservations (tool_id, borrower_id, start_date, end_date, status)
                VALUES (:tool_id, :borrower_id, :start_date, :end_date, 'pending')
            """),
            {
                "tool_id": tool_id,
                "borrower_id": user["user_id"],
                "start_date": start_date,
                "end_date": end_date
            }
        )
        db.commit()
        return RedirectResponse(
            url="/dashboard?message=Reservation created successfully!",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = str(e)
        # Check if this is a trigger exception
        if "already reserved" in error_msg.lower():
            return RedirectResponse(
                url="/dashboard?error=TRIGGER FIRED: This tool is already reserved for the selected dates!",
                status_code=303
            )
        elif "your own tool" in error_msg.lower():
            return RedirectResponse(
                url="/dashboard?error=TRIGGER FIRED: You cannot reserve your own tool!",
                status_code=303
            )
        return RedirectResponse(url=f"/dashboard?error=Reservation failed: {error_msg}", status_code=303)


@app.post("/reservations/update/{reservation_id}")
async def update_reservation_status(
    request: Request,
    reservation_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db)
):
    """Update reservation status (approve, complete, cancel)."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        db.execute(
            text("""
                UPDATE reservations r
                SET status = :status, last_updated = CURRENT_TIMESTAMP
                WHERE r.reservation_id = :reservation_id
                  AND EXISTS (
                      SELECT 1 FROM tools t 
                      WHERE t.tool_id = r.tool_id 
                        AND (t.owner_id = :user_id OR :is_admin = true)
                  )
            """),
            {
                "reservation_id": reservation_id,
                "status": status,
                "user_id": user["user_id"],
                "is_admin": user["role"] == "admin"
            }
        )
        db.commit()
        return RedirectResponse(
            url=f"/dashboard?message=Reservation status updated to '{status}'!",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Update failed: {str(e)}", status_code=303)


@app.post("/reservations/delete/{reservation_id}")
async def delete_reservation(
    request: Request,
    reservation_id: int,
    db: Session = Depends(get_db)
):
    """Cancel/delete a reservation."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        db.execute(
            text("""
                DELETE FROM reservations 
                WHERE reservation_id = :reservation_id 
                  AND (borrower_id = :user_id OR :is_admin = true)
            """),
            {
                "reservation_id": reservation_id,
                "user_id": user["user_id"],
                "is_admin": user["role"] == "admin"
            }
        )
        db.commit()
        return RedirectResponse(url="/dashboard?message=Reservation cancelled!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Cancellation failed: {str(e)}", status_code=303)


# =============================================================================
# RATING ROUTES
# =============================================================================

@app.post("/ratings/add")
async def add_rating(
    request: Request,
    reservation_id: int = Form(...),
    rated_user_id: int = Form(...),
    score: int = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Submit a rating for a completed transaction.
    
    The CHECK constraint ensures score is between 1-5.
    The trg_update_user_trust_score trigger will update the user's trust score.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Please login first", status_code=303)
    
    try:
        db.execute(
            text("""
                INSERT INTO ratings (reservation_id, rater_id, rated_user_id, score, comment)
                VALUES (:reservation_id, :rater_id, :rated_user_id, :score, :comment)
            """),
            {
                "reservation_id": reservation_id,
                "rater_id": user["user_id"],
                "rated_user_id": rated_user_id,
                "score": score,
                "comment": comment
            }
        )
        db.commit()
        return RedirectResponse(
            url="/dashboard?message=Rating submitted! (Trigger updated user trust score)",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = str(e)
        if "chk_rating_score" in error_msg.lower():
            return RedirectResponse(
                url="/dashboard?error=CHECK CONSTRAINT: Score must be between 1 and 5!",
                status_code=303
            )
        return RedirectResponse(url=f"/dashboard?error=Rating failed: {error_msg}", status_code=303)


# =============================================================================
# ADMIN ROUTES
# =============================================================================

@app.post("/admin/users/delete/{user_id}")
async def admin_delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    """Admin: Delete a user."""
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/dashboard?error=Admin access required", status_code=303)
    
    if user_id == user["user_id"]:
        return RedirectResponse(url="/dashboard?error=Cannot delete yourself", status_code=303)
    
    try:
        db.execute(
            text("DELETE FROM users WHERE user_id = :user_id"),
            {"user_id": user_id}
        )
        db.commit()
        return RedirectResponse(url="/dashboard?message=User deleted successfully!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Delete failed: {str(e)}", status_code=303)


# =============================================================================
# RUN APPLICATION
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
