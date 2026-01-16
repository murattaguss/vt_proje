from database import get_db_connection
from sqlalchemy import text

def fix_overlap_logic():
    print("Fixing overlap logic...")
    with get_db_connection() as conn:
        try:
            # Re-create check_tool_availability with STRICT inequalities
            print("Updating check_tool_availability...")
            conn.execute(text("""
                CREATE OR REPLACE FUNCTION check_tool_availability(
                    p_tool_id INTEGER,
                    p_start_date TIMESTAMP,
                    p_end_date TIMESTAMP
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
                          (start_date < p_end_date AND end_date > p_start_date)
                      );
                    
                    RETURN v_conflict_count = 0;
                END;
                $$;
            """))

            # Re-create fn_prevent_double_booking with STRICT inequalities
            print("Updating fn_prevent_double_booking...")
            conn.execute(text("""
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
                      AND (start_date < NEW.end_date AND end_date > NEW.start_date);
                    
                    IF v_conflict_count > 0 THEN
                        RAISE EXCEPTION 'This tool is already reserved for the selected dates.';
                    END IF;
                    
                    RETURN NEW;
                END;
                $$;
            """))

            conn.commit()
            print("Logic updated successfully!")
        except Exception as e:
            conn.rollback()
            print(f"Logic update failed: {e}")

if __name__ == "__main__":
    fix_overlap_logic()
