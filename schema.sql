-- ToolShare Veritabanı Şeması

-- Varsa mevcut nesneleri temizle
DROP TRIGGER IF EXISTS trg_update_timestamp ON tools;
DROP TRIGGER IF EXISTS trg_prevent_double_booking ON reservations;
DROP TRIGGER IF EXISTS trg_update_user_trust_score ON ratings;
DROP FUNCTION IF EXISTS fn_update_timestamp();
DROP FUNCTION IF EXISTS fn_prevent_double_booking();
DROP FUNCTION IF EXISTS fn_update_trust_score();
DROP FUNCTION IF EXISTS calculate_trust_score(INTEGER);
DROP FUNCTION IF EXISTS get_user_activity_report(INTEGER);
DROP FUNCTION IF EXISTS check_tool_availability(INTEGER, DATE, DATE);
DROP VIEW IF EXISTS v_available_tools;
DROP TABLE IF EXISTS ratings;
DROP TABLE IF EXISTS reservations;
DROP TABLE IF EXISTS tools;
DROP TABLE IF EXISTS users;
DROP SEQUENCE IF EXISTS tool_seq;


-- SIRA (SEQUENCE): Aletler için özel ID üretimi
CREATE SEQUENCE tool_seq
    START WITH 1000
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


-- TABLO: Users (Kullanıcılar)
-- Rol tabanlı erişim ile kullanıcı hesap bilgilerini saklar
CREATE TABLE users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(10) NOT NULL DEFAULT 'user',
    trust_score DECIMAL(3,2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_user_role CHECK (role IN ('admin', 'user')),
    CONSTRAINT chk_trust_score CHECK (trust_score >= 0 AND trust_score <= 5)
);

-- TABLO: Tools (Aletler)
-- Sahip referansı ile alet listelerini saklar
CREATE TABLE tools (
    tool_id INTEGER PRIMARY KEY DEFAULT nextval('tool_seq'),
    owner_id INTEGER NOT NULL,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    category VARCHAR(50),
    status VARCHAR(20) DEFAULT 'available',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_tool_owner FOREIGN KEY (owner_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT chk_tool_status CHECK (status IN ('available', 'reserved', 'maintenance'))
);

-- TABLO: Reservations (Rezervasyonlar)
-- Alet kiralama kayıtlarını saklar
CREATE TABLE reservations (
    reservation_id SERIAL PRIMARY KEY,
    tool_id INTEGER NOT NULL,
    borrower_id INTEGER NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_reservation_tool FOREIGN KEY (tool_id) REFERENCES tools(tool_id) ON DELETE CASCADE,
    CONSTRAINT fk_reservation_borrower FOREIGN KEY (borrower_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT chk_reservation_dates CHECK (end_date >= start_date),
    CONSTRAINT chk_reservation_status CHECK (status IN ('pending', 'approved', 'completed', 'cancelled'))
);


-- TABLO: Ratings (Puanlar)
-- KONTROL kısıtlaması (1-5) ile kullanıcı puanlarını saklar
CREATE TABLE ratings (
    rating_id SERIAL PRIMARY KEY,
    reservation_id INTEGER NOT NULL,
    rater_id INTEGER NOT NULL,
    rated_user_id INTEGER NOT NULL,
    score INTEGER NOT NULL,
    comment TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rating_reservation FOREIGN KEY (reservation_id) REFERENCES reservations(reservation_id) ON DELETE CASCADE,
    CONSTRAINT fk_rating_rater FOREIGN KEY (rater_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_rating_rated FOREIGN KEY (rated_user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT chk_rating_score CHECK (score >= 1 AND score <= 5)
);


-- İNDEKS: Arama optimizasyonu için alet adı üzerinde
CREATE INDEX idx_tool_name ON tools(name);
CREATE INDEX idx_tool_category ON tools(category);
CREATE INDEX idx_reservation_dates ON reservations(start_date, end_date);


-- GÖRÜNÜM (VIEW): Rezervasyon için müsait aletler
CREATE VIEW v_available_tools AS
SELECT 
    t.tool_id,
    t.name,
    t.description,
    t.category,
    u.username AS owner_name,
    u.trust_score AS owner_trust_score
FROM tools t
JOIN users u ON t.owner_id = u.user_id
WHERE t.status = 'available';


-- FONKSİYON 1: Kullanıcı güven skorunu hesapla (ortalama puan)
CREATE OR REPLACE FUNCTION calculate_trust_score(p_user_id INTEGER)
RETURNS DECIMAL(3,2)
LANGUAGE plpgsql
AS $$
DECLARE
    v_score DECIMAL(3,2);
BEGIN
    SELECT COALESCE(AVG(score)::DECIMAL(3,2), 0.00)
    INTO v_score
    FROM ratings
    WHERE rated_user_id = p_user_id;
    
    RETURN v_score;
END;
$$;

-- FONKSİYON 2: RECORD ve CURSOR (İMLEÇ) kullanarak kullanıcı aktivite raporunu getir
-- Bu fonksiyon kullanıcının rezervasyonları üzerinde döner ve bir rapor oluşturur
CREATE OR REPLACE FUNCTION get_user_activity_report(p_user_id INTEGER)
RETURNS TABLE(
    activity_type VARCHAR,
    tool_name VARCHAR,
    partner_name VARCHAR,
    activity_date DATE,
    status VARCHAR
)
LANGUAGE plpgsql
AS $$
DECLARE
    v_record RECORD;
    v_cursor CURSOR FOR
        SELECT 
            'borrowed' AS type,
            t.name AS tool_name,
            owner.username AS partner,
            r.start_date AS activity_date,
            r.status
        FROM reservations r
        JOIN tools t ON r.tool_id = t.tool_id
        JOIN users owner ON t.owner_id = owner.user_id
        WHERE r.borrower_id = p_user_id
        UNION ALL
        SELECT 
            'lent' AS type,
            t.name AS tool_name,
            borrower.username AS partner,
            r.start_date AS activity_date,
            r.status
        FROM reservations r
        JOIN tools t ON r.tool_id = t.tool_id
        JOIN users borrower ON r.borrower_id = borrower.user_id
        WHERE t.owner_id = p_user_id
        ORDER BY activity_date DESC;
BEGIN
    OPEN v_cursor;
    LOOP
        FETCH v_cursor INTO v_record;
        EXIT WHEN NOT FOUND;
        
        activity_type := v_record.type;
        tool_name := v_record.tool_name;
        partner_name := v_record.partner;
        activity_date := v_record.activity_date;
        status := v_record.status;
        
        RETURN NEXT;
    END LOOP;
    CLOSE v_cursor;
END;
$$;

-- FONKSİYON 3: Tarih aralığı için alet müsaitliğini kontrol et
CREATE OR REPLACE FUNCTION check_tool_availability(
    p_tool_id INTEGER,
    p_start_date DATE,
    p_end_date DATE
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_conflict_count INTEGER;
BEGIN
    SELECT COUNT(*)
    INTO v_conflict_count
    FROM reservations
    WHERE tool_id = p_tool_id
      AND status IN ('pending', 'approved')
      AND (
          (start_date <= p_end_date AND end_date >= p_start_date)
      );
    
    RETURN v_conflict_count = 0;
END;
$$;

-- TETİKLEYİCİ FONKSİYONU: Zaman damgasını otomatik güncelle
CREATE OR REPLACE FUNCTION fn_update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.last_updated := CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;


-- TETİKLEYİCİ FONKSİYONU: Çifte rezervasyonu engelle
CREATE OR REPLACE FUNCTION fn_prevent_double_booking()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_conflict_count INTEGER;
    v_tool_owner_id INTEGER;
BEGIN
    -- Kullanıcının kendi aletini rezerve etmeye çalışıp çalışmadığını kontrol et
    SELECT owner_id INTO v_tool_owner_id
    FROM tools
    WHERE tool_id = NEW.tool_id;
    
    IF v_tool_owner_id = NEW.borrower_id THEN
        RAISE EXCEPTION 'You cannot reserve your own tool.';
    END IF;
    
    -- Çakışan rezervasyonları kontrol et
    SELECT COUNT(*)
    INTO v_conflict_count
    FROM reservations
    WHERE tool_id = NEW.tool_id
      AND reservation_id != COALESCE(NEW.reservation_id, 0)
      AND status IN ('pending', 'approved')
      AND (start_date <= NEW.end_date AND end_date >= NEW.start_date);
    
    IF v_conflict_count > 0 THEN
        RAISE EXCEPTION 'This tool is already reserved for the selected dates.';
    END IF;
    
    RETURN NEW;
END;
$$;

-- TETİKLEYİCİ FONKSİYONU: Puanlamadan sonra kullanıcı güven skorunu güncelle
CREATE OR REPLACE FUNCTION fn_update_trust_score()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE users
    SET trust_score = calculate_trust_score(NEW.rated_user_id)
    WHERE user_id = NEW.rated_user_id;
    
    RETURN NEW;
END;
$$;

-- TETİKLEYİCİ 1: Tools tablosunda zaman damgasını otomatik güncelle
CREATE TRIGGER trg_update_timestamp
    BEFORE UPDATE ON tools
    FOR EACH ROW
    EXECUTE FUNCTION fn_update_timestamp();

-- TETİKLEYİCİ 2: Rezervasyonlarda çifte rezervasyonu engelle
CREATE TRIGGER trg_prevent_double_booking
    BEFORE INSERT OR UPDATE ON reservations
    FOR EACH ROW
    EXECUTE FUNCTION fn_prevent_double_booking();


-- TETİKLEYİCİ 3: Puanlamadan sonra güven skorunu otomatik güncelle
CREATE TRIGGER trg_update_user_trust_score
    AFTER INSERT ON ratings
    FOR EACH ROW
    EXECUTE FUNCTION fn_update_trust_score();


-- ÖRNEK VERİLER
INSERT INTO users (username, email, password_hash, role, trust_score) VALUES
('admin', 'admin@toolshare.com', 'pbkdf2:sha256:260000$admin123', 'admin', 5.00),
('ahmet_yilmaz', 'ahmet@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.50),
('mehmet_demir', 'mehmet@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.20),
('ayse_kaya', 'ayse@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.80),
('fatma_celik', 'fatma@email.com', 'pbkdf2:sha256:260000$user123', 'user', 3.90),
('ali_ozturk', 'ali@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.10),
('zeynep_arslan', 'zeynep@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.60),
('mustafa_sahin', 'mustafa@email.com', 'pbkdf2:sha256:260000$user123', 'user', 3.70),
('elif_yildiz', 'elif@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.40),
('emre_aksoy', 'emre@email.com', 'pbkdf2:sha256:260000$user123', 'user', 4.00);


INSERT INTO tools (owner_id, name, description, category, status) VALUES
(2, 'Bosch Matkap', 'Professional darbesiz matkap, 750W', 'Elektrikli Alet', 'available'),
(2, 'Cim Bicme Makinesi', 'Benzinli cim bicme makinesi, genis kesim', 'Bahce', 'available'),
(3, 'Elektrikli Testere', 'Stihl elektrikli testere, agac kesimi icin ideal', 'Elektrikli Alet', 'available'),
(4, 'Merdiven', '6 metre aluminyum merdiven', 'El Aleti', 'available'),
(4, 'Basincli Yikama', 'Karcher basincli yikama makinesi', 'Temizlik', 'reserved'),
(5, 'Kaynak Makinesi', 'Inverter kaynak makinesi, 200A', 'Elektrikli Alet', 'available'),
(6, 'Jenerator', '5kW benzinli jenerator', 'Elektrikli Alet', 'available'),
(7, 'Beton Mikser', 'Elektrikli beton mikser, 120L', 'Insaat', 'available'),
(8, 'Oto Krikosu', 'Hidrolik oto krikosu, 3 ton', 'Otomotiv', 'available'),
(9, 'Zimpara Makinesi', 'Elektrikli zimpara, titresimli', 'Elektrikli Alet', 'available');

INSERT INTO reservations (tool_id, borrower_id, start_date, end_date, status) VALUES
(1000, 3, '2026-01-10', '2026-01-12', 'completed'),
(1001, 4, '2026-01-15', '2026-01-16', 'completed'),
(1002, 2, '2026-01-18', '2026-01-20', 'completed'),
(1003, 5, '2026-01-20', '2026-01-21', 'approved'),
(1004, 6, '2026-01-14', '2026-01-17', 'approved'),
(1005, 7, '2026-01-22', '2026-01-25', 'pending'),
(1006, 8, '2026-01-25', '2026-01-28', 'pending'),
(1007, 9, '2026-01-20', '2026-01-22', 'completed'),
(1008, 10, '2026-01-28', '2026-01-30', 'pending'),
(1009, 2, '2026-01-30', '2026-02-01', 'pending');

INSERT INTO ratings (reservation_id, rater_id, rated_user_id, score, comment) VALUES
(1, 3, 2, 5, 'Mükemmel alet, sorunsuz çalışıyor. Kesinlikle tavsiye ederim!'),
(1, 2, 3, 4, 'İyi bir kullanıcı, zamanında teslim etti.'),
(2, 4, 2, 5, 'Harika çim biçme makinesi, çok bakımlı.'),
(2, 2, 4, 5, 'Mükemmel kullanıcı, aleti çok dikkatli kullandı.'),
(3, 2, 3, 4, 'İyi testere, işimi gördü.'),
(3, 3, 2, 5, 'Güvenilir kullanıcı.'),
(4, 5, 4, 4, 'Sağlam merdiven, kullanımı güvenli.'),
(8, 9, 7, 5, 'Harika beton mikseri, bana çok zaman kazandırdı.'),
(8, 7, 9, 4, 'Sorumlu bir kullanıcı.'),
(5, 6, 4, 5, 'Güçlü bir yıkama makinesi, her şeyi temizledi!');