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

# FastAPI uygulamasını başlat
app = FastAPI(
    title="ToolShare",
    description="Mahalle Alet ve Ekipman Paylaşım Platformu",
    version="1.0.0"
)

# Şablon yapılandırması
templates = Jinja2Templates(directory="templates")

# Basit oturum saklama
user_sessions = {}

# YARDIMCI FONKSİYONLAR
def get_current_user(request: Request) -> Optional[dict]:
    """
    Oturum bilgisinden mevcut giriş yapmış kullanıcıyı getirir.
    
    Args:
        request: FastAPI istek nesnesi.
        
    Returns:
        dict: Giriş yapılmışsa kullanıcı verisi, aksi halde None.
    """
    session_id = request.cookies.get("session_id")
    if session_id and session_id in user_sessions:
        return user_sessions[session_id]
    return None


def require_login(request: Request) -> dict:
    """
    Kullanıcının giriş yapmış olmasını gerektirir.
    
    Args:
        request: FastAPI istek nesnesi.
        
    Returns:
        dict: Kullanıcı verisi.
        
    Raises:
        HTTPException: Kullanıcı giriş yapmamışsa.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Lütfen önce giriş yapın")
    return user


# KİMLİK DOĞRULAMA ROTALARI
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Giriş/Kayıt sayfasını render eder (gösterir)."""
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
    Kullanıcı girişini işler.
    
    Args:
        username: Kullanıcı adı.
        password: Şifre.
        db: Veritabanı oturumu.
        
    Returns:
        RedirectResponse: Başarılı ise panele yönlendirme.
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
            return RedirectResponse(url="/?error=Geçersiz kullanıcı adı veya şifre", status_code=303)
    except SQLAlchemyError as e:
        return RedirectResponse(url=f"/?error=Veritanabı hatası: {str(e)}", status_code=303)


@app.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Kullanıcı kaydını işler.
    
    Args:
        username: İstenen kullanıcı adı.
        email: Kullanıcı e-postası.
        password: Şifre.
        db: Veritabanı oturumu.
        
    Returns:
        RedirectResponse: Başarılı ise giriş sayfasına yönlendirme.
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
        return RedirectResponse(url="/?message=Kayıt başarılı! Lütfen giriş yapın.", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = str(e)
        if "unique" in error_msg.lower():
            return RedirectResponse(url="/?error=Kullanıcı adı veya e-posta zaten mevcut", status_code=303)
        return RedirectResponse(url=f"/?error=Kayıt başarısız: {error_msg}", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    """Kullanıcı çıkışını işler."""
    session_id = request.cookies.get("session_id")
    if session_id and session_id in user_sessions:
        del user_sessions[session_id]
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_id")
    return response


# KULLANICI PANELİ ROTALARI
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """
    Tüm özellikleriyle kullanıcı panelini gösterir.
    
    Aletler, rezervasyonlar, puanlar ve varsa yönetici özelliklerini içerir.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
    message = request.query_params.get("message", "")
    error = request.query_params.get("error", "")
    
    try:
        # Müsait aletleri görüntüle 
        available_tools = db.execute(
            text("SELECT * FROM v_available_tools")
        ).fetchall()
        
        # Kullanıcının kendi aletlerini görüntüle
        my_tools = db.execute(
            text("SELECT * FROM tools WHERE owner_id = :user_id"),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # Kullanıcının kendi rezervasyonlarını görüntüle
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
        
        # Kullanıcının borcu olan aletlerini görüntüle
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
        
        # Kullanıcı tarafından verilen puanları görüntüle
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
        
        # Kullanıcı aktivitesi raporunu görüntüle
        activity_report = db.execute(
            text("SELECT * FROM get_user_activity_report(:user_id)"),
            {"user_id": user["user_id"]}
        ).fetchall()
        
        # En iyi puan alan kullanıcıları görüntüle (puan > 4.0)
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
        
        # Kullanıcı borcu olan aletleri görüntüle
        never_reserved_tools = db.execute(
            text("""
                SELECT tool_id, name, category FROM tools
                EXCEPT
                SELECT DISTINCT t.tool_id, t.name, t.category 
                FROM tools t
                JOIN reservations r ON t.tool_id = r.tool_id
            """)
        ).fetchall()
        
        # Admin yetkili kullanıcılar için tüm kullanıcı ve aletleri görüntüle
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
        
        # Müsait aletler
        all_available_tools = db.execute(
            text("SELECT tool_id, name FROM tools WHERE status = 'available'")
        ).fetchall()
        
        # Puan verilecek rezervasyonlar
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


# ALET ROTALARI
@app.post("/tools/add")
async def add_tool(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    category: str = Form(""),
    db: Session = Depends(get_db)
):
    """
    Yeni bir alet ilanı ekler.
    
    ID üretimi için tool_seq SIRA (SEQUENCE) nesnesini kullanır.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
    try:
        # Alet ekleme
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
        return RedirectResponse(url="/dashboard?message=Alet başarıyla eklendi!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Alet eklenemedi: {str(e)}", status_code=303)


@app.post("/tools/delete/{tool_id}")
async def delete_tool(
    request: Request,
    tool_id: int,
    db: Session = Depends(get_db)
):
    """Bir alet ilanını siler."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
    try:
        # Sahiplik kontrolü
        result = db.execute(
            text("SELECT owner_id FROM tools WHERE tool_id = :tool_id"),
            {"tool_id": tool_id}
        ).fetchone()
        
        if not result:
            return RedirectResponse(url="/dashboard?error=Alet bulunamadı", status_code=303)
        
        if result[0] != user["user_id"] and user["role"] != "admin":
            return RedirectResponse(url="/dashboard?error=Sadece kendi aletlerinizi silebilirsiniz", status_code=303)
        
        db.execute(
            text("DELETE FROM tools WHERE tool_id = :tool_id"),
            {"tool_id": tool_id}
        )
        db.commit()
        return RedirectResponse(url="/dashboard?message=Alet başarıyla silindi!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Alet silinemedi: {str(e)}", status_code=303)


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
    Bir alet ilanını günceller.
    
    Bu işlem last_updated alanını güncellemek için trg_update_timestamp tetikleyicisini çalıştırır.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
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
            url="/dashboard?message=Alet güncellendi! (Tetikleyici zaman damgasını güncelledi)",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Alet güncellenemedi: {str(e)}", status_code=303)


@app.get("/tools/search", response_class=HTMLResponse)
async def search_tools(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db)
):
    """
    İsme göre alet araması yapar.
    
    Optimize edilmiş arama için idx_tool_name INDEX'ini kullanır.
    """
    user = get_current_user(request)
    
    try:
        # İndekslenmiş sütunu kullanarak arama yap
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
                "message": f"'{q}' ile eşleşen {len(results)} alet bulundu",
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
        return RedirectResponse(url=f"/dashboard?error=Arama başarısız: {str(e)}", status_code=303)


# REZERVASYON ROTALARI
@app.post("/reservations/add")
async def add_reservation(
    request: Request,
    tool_id: int = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Yeni bir rezervasyon oluşturur.
    
    trg_prevent_double_booking tetikleyicisi çakışmaları kontrol eder
    ve alet zaten rezerve edilmişse bir istisna (exception) fırlatır.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
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
            url="/dashboard?message=Rezervasyon başarıyla oluşturuldu!",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = str(e)
        # Tetikleyici hata mesajı kontrolü
        if "already reserved" in error_msg.lower():
            return RedirectResponse(
                url="/dashboard?error=TETİKLEYİCİ ÇALIŞTI: Bu alet seçilen tarihler için zaten rezerve edilmiş!",
                status_code=303
            )
        elif "your own tool" in error_msg.lower():
            return RedirectResponse(
                url="/dashboard?error=TETİKLEYİCİ ÇALIŞTI: Kendi aletinizi rezerve edemezsiniz!",
                status_code=303
            )
        return RedirectResponse(url=f"/dashboard?error=Rezervasyon başarısız: {error_msg}", status_code=303)


@app.post("/reservations/update/{reservation_id}")
async def update_reservation_status(
    request: Request,
    reservation_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db)
):
    """Rezervasyon durumunu günceller (onayla, tamamla, iptal et)."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
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
            url=f"/dashboard?message=Rezervasyon durumu '{status}' olarak güncellendi!",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Güncelleme başarısız: {str(e)}", status_code=303)


@app.post("/reservations/delete/{reservation_id}")
async def delete_reservation(
    request: Request,
    reservation_id: int,
    db: Session = Depends(get_db)
):
    """Bir rezervasyonu iptal eder/siler."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
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
        return RedirectResponse(url="/dashboard?message=Rezervasyon iptal edildi!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=İptal başarısız: {str(e)}", status_code=303)


# PUANLAMA ROTALARI
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
    Tamamlanan bir işlem için puan gönderir.
    
    CHECK kısıtlaması (CONSTRAINT) puanın 1-5 arasında olmasını sağlar.
    trg_update_user_trust_score tetikleyicisi kullanıcının güven skorunu günceller.
    """
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/?error=Lütfen önce giriş yapın", status_code=303)
    
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
            url="/dashboard?message=Puan gönderildi! (Tetikleyici  güven skorunu güncelledi)",
            status_code=303
        )
    except SQLAlchemyError as e:
        db.rollback()
        error_msg = str(e)
        if "chk_rating_score" in error_msg.lower():
            return RedirectResponse(
                url="/dashboard?error=CHECK CONSTRAINT (KONTROL KISITLAMASI): Puan 1 ve 5 arasında olmalıdır!",
                status_code=303
            )
        return RedirectResponse(url=f"/dashboard?error=Puanlama başarısız: {error_msg}", status_code=303)


# YÖNETİCİ (ADMIN) ROTALARI
@app.post("/admin/users/delete/{user_id}")
async def admin_delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    """Yönetici: Bir kullanıcıyı siler."""
    user = get_current_user(request)
    if not user or user["role"] != "admin":
        return RedirectResponse(url="/dashboard?error=Yönetici izni gerekli", status_code=303)
    
    if user_id == user["user_id"]:
        return RedirectResponse(url="/dashboard?error=Kendinizi silemezsiniz", status_code=303)
    
    try:
        db.execute(
            text("DELETE FROM users WHERE user_id = :user_id"),
            {"user_id": user_id}
        )
        db.commit()
        return RedirectResponse(url="/dashboard?message=Kullanıcı başarıyla silindi!", status_code=303)
    except SQLAlchemyError as e:
        db.rollback()
        return RedirectResponse(url=f"/dashboard?error=Silme işlemi başarısız: {str(e)}", status_code=303)


# main 
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
