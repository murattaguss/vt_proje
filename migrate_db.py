from database import get_db_connection
from sqlalchemy import text

def run_migration():
    print("Starting migration...")
    with get_db_connection() as conn:
        try:
            # 1. Alter table
            print("Altering reservations table...")
            conn.execute(text("ALTER TABLE reservations ALTER COLUMN start_date TYPE TIMESTAMP USING start_date::timestamp"))
            conn.execute(text("ALTER TABLE reservations ALTER COLUMN end_date TYPE TIMESTAMP USING end_date::timestamp"))
            
            # 2. Drop old functions to avoid signature conflicts or stale definitions
            print("Dropping old functions...")
            conn.execute(text("DROP FUNCTION IF EXISTS check_tool_availability(INTEGER, DATE, DATE)"))
            conn.execute(text("DROP FUNCTION IF EXISTS get_user_activity_report(INTEGER)"))
            
            # 3. Re-create check_tool_availability with TIMESTAMP
            print("Creating check_tool_availability...")
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
                          (start_date <= p_end_date AND end_date >= p_start_date)
                      );
                    
                    RETURN v_conflict_count = 0;
                END;
                $$;
            """))

            # 4. Re-create get_user_activity_report with TIMESTAMP
            print("Creating get_user_activity_report...")
            conn.execute(text("""
                CREATE OR REPLACE FUNCTION get_user_activity_report(p_user_id INTEGER)
                RETURNS TABLE(
                    activity_type VARCHAR,
                    tool_name VARCHAR,
                    partner_name VARCHAR,
                    activity_date TIMESTAMP,
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
            """))

            conn.commit()
            print("Migration completed successfully!")
        except Exception as e:
            conn.rollback()
            print(f"Migration failed: {e}")

if __name__ == "__main__":
    run_migration()
