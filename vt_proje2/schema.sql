-- ============================================================================
-- ToolShare Database Schema
-- Neighborhood Tool & Equipment Sharing Platform
-- ============================================================================

-- Clean up existing objects if they exist
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

-- ============================================================================
-- SEQUENCE: Custom ID generation for tools
-- ============================================================================
CREATE SEQUENCE tool_seq
    START WITH 1000
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

-- ============================================================================
-- TABLE: Users
-- Stores user account information with role-based access
-- ============================================================================
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

-- ============================================================================
-- TABLE: Tools
-- Stores tool listings with owner reference
-- ============================================================================
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

-- ============================================================================
-- TABLE: Reservations
-- Stores tool booking records
-- ============================================================================
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

-- ============================================================================
-- TABLE: Ratings
-- Stores user ratings with CHECK constraint (1-5)
-- ============================================================================
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

-- ============================================================================
-- INDEX: On tool name for search optimization
-- ============================================================================
CREATE INDEX idx_tool_name ON tools(name);
CREATE INDEX idx_tool_category ON tools(category);
CREATE INDEX idx_reservation_dates ON reservations(start_date, end_date);

-- ============================================================================
-- VIEW: Available tools for reservation
-- ============================================================================
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

-- ============================================================================
-- FUNCTION 1: Calculate user trust score (average rating)
-- ============================================================================
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

-- ============================================================================
-- FUNCTION 2: Get user activity report using RECORD and CURSOR
-- This function iterates through user's reservations and generates a report
-- ============================================================================
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

-- ============================================================================
-- FUNCTION 3: Check tool availability for date range
-- ============================================================================
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

-- ============================================================================
-- TRIGGER FUNCTION: Update timestamp automatically
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_update_timestamp()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.last_updated := CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

-- ============================================================================
-- TRIGGER FUNCTION: Prevent double booking
-- ============================================================================
CREATE OR REPLACE FUNCTION fn_prevent_double_booking()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_conflict_count INTEGER;
    v_tool_owner_id INTEGER;
BEGIN
    -- Check if user is trying to reserve their own tool
    SELECT owner_id INTO v_tool_owner_id
    FROM tools
    WHERE tool_id = NEW.tool_id;
    
    IF v_tool_owner_id = NEW.borrower_id THEN
        RAISE EXCEPTION 'You cannot reserve your own tool.';
    END IF;
    
    -- Check for overlapping reservations
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

-- ============================================================================
-- TRIGGER FUNCTION: Update user trust score after rating
-- ============================================================================
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

-- ============================================================================
-- TRIGGER 1: Auto-update timestamp on tools table
-- ============================================================================
CREATE TRIGGER trg_update_timestamp
    BEFORE UPDATE ON tools
    FOR EACH ROW
    EXECUTE FUNCTION fn_update_timestamp();

-- ============================================================================
-- TRIGGER 2: Prevent double booking on reservations
-- ============================================================================
CREATE TRIGGER trg_prevent_double_booking
    BEFORE INSERT OR UPDATE ON reservations
    FOR EACH ROW
    EXECUTE FUNCTION fn_prevent_double_booking();

-- ============================================================================
-- TRIGGER 3: Auto-update trust score after rating
-- ============================================================================
CREATE TRIGGER trg_update_user_trust_score
    AFTER INSERT ON ratings
    FOR EACH ROW
    EXECUTE FUNCTION fn_update_trust_score();

-- ============================================================================
-- SAMPLE DATA: 10 records per table
-- ============================================================================

-- Insert Users (10 records)
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

-- Insert Tools (10 records)
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

-- Insert Reservations (10 records)
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

-- Insert Ratings (10 records)
INSERT INTO ratings (reservation_id, rater_id, rated_user_id, score, comment) VALUES
(1, 3, 2, 5, 'Excellent tool, works perfectly. Highly recommended!'),
(1, 2, 3, 4, 'Good borrower, returned on time.'),
(2, 4, 2, 5, 'Great lawnmower, very well maintained.'),
(2, 2, 4, 5, 'Perfect borrower, very careful with the equipment.'),
(3, 2, 3, 4, 'Good chainsaw, did the job well.'),
(3, 3, 2, 5, 'Trustworthy borrower.'),
(4, 5, 4, 4, 'Sturdy ladder, safe to use.'),
(8, 9, 7, 5, 'Excellent concrete mixer, saved me a lot of time.'),
(8, 7, 9, 4, 'Responsible borrower.'),
(5, 6, 4, 5, 'Powerful pressure washer, cleaned everything!');

-- ============================================================================
-- USEFUL QUERIES FOR DEMONSTRATION
-- ============================================================================

-- Query 1: SET OPERATION - Tools that have NEVER been reserved (EXCEPT)
-- SELECT tool_id, name FROM tools
-- EXCEPT
-- SELECT DISTINCT t.tool_id, t.name FROM tools t
-- JOIN reservations r ON t.tool_id = r.tool_id;

-- Query 2: AGGREGATE with HAVING - Users with average rating > 4.0
-- SELECT u.username, AVG(r.score) as avg_rating
-- FROM users u
-- JOIN ratings r ON u.user_id = r.rated_user_id
-- GROUP BY u.user_id, u.username
-- HAVING AVG(r.score) > 4.0
-- ORDER BY avg_rating DESC;

-- Query 3: Using the VIEW
-- SELECT * FROM v_available_tools;

-- Query 4: Using the FUNCTION with CURSOR
-- SELECT * FROM get_user_activity_report(2);
