
import sys
import oracledb

from config import Config


def initialize_oracle_client():
    """Initialize Oracle Instant Client."""

    print("=" * 60)
    print("STEP 1: INITIALIZE ORACLE CLIENT")
    print("=" * 60)

    try:
        client_dir = Config.ORACLE_CLIENT_LIB_DIR

        print(f"Client directory: {client_dir}")

        oracledb.init_oracle_client(
            lib_dir=client_dir
        )

        print(f"Oracle Client version: {oracledb.clientversion()}")
        print(f"Thin Mode: {oracledb.is_thin_mode()}")

        if oracledb.is_thin_mode():
            raise RuntimeError(
                "Oracle is still running in Thin Mode!"
            )

        print("[SUCCESS] Thick Mode initialized.")

        return True

    except Exception as error:
        print("[FAILED] Oracle Client initialization.")
        print(f"Error: {error}")
        return False


def test_oracle_connection():
    """Test direct Oracle Database connection."""

    connection = None
    cursor = None

    print("\n" + "=" * 60)
    print("STEP 2: ORACLE DATABASE CONNECTION")
    print("=" * 60)

    print(f"DB Host: {Config.DB_HOST}")
    print(f"DB Port: {Config.DB_PORT}")
    print(f"DB Service: {Config.DB_SERVICE}")
    print(f"DB Schema: {Config.DB_SCHEMA}")
    print(f"DB User: {Config.DB_USER}")
    print(f"DB DSN: {Config.DB_DSN}")

    try:
        print("\nConnecting to Oracle...")

        connection = oracledb.connect(
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            dsn=Config.DB_DSN,
            tcp_connect_timeout=Config.DB_CONNECT_TIMEOUT
        )

        print("[SUCCESS] Connected to Oracle Database!")

        cursor = connection.cursor()

        # --------------------------------------------------
        # STEP 3: CHECK DATABASE INFORMATION
        # --------------------------------------------------

        print("\n" + "=" * 60)
        print("STEP 3: DATABASE INFORMATION")
        print("=" * 60)

        cursor.execute("""
            SELECT
                USER,
                SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA'),
                SYS_CONTEXT('USERENV', 'DB_NAME'),
                SYS_CONTEXT('USERENV', 'SERVICE_NAME'),
                SYS_CONTEXT('USERENV', 'SERVER_HOST'),
                SYS_CONTEXT('USERENV', 'INSTANCE_NAME')
            FROM DUAL
        """)

        row = cursor.fetchone()

        print(f"Connected User : {row[0]}")
        print(f"Current Schema : {row[1]}")
        print(f"Database Name  : {row[2]}")
        print(f"Service Name   : {row[3]}")
        print(f"Server Host    : {row[4]}")
        print(f"Instance Name  : {row[5]}")

        # --------------------------------------------------
        # STEP 4: CHECK TARGET SCHEMA
        # --------------------------------------------------

        print("\n" + "=" * 60)
        print("STEP 4: CHECK APPS TABLE ACCESS")
        print("=" * 60)

        cursor.execute("""
            SELECT COUNT(*)
            FROM ALL_TABLES
            WHERE OWNER = :schema
        """, {
            "schema": Config.DB_SCHEMA
        })

        table_count = cursor.fetchone()[0]

        print(f"Accessible tables in {Config.DB_SCHEMA}: {table_count}")

        # --------------------------------------------------
        # STEP 5: DISPLAY SAMPLE TABLES
        # --------------------------------------------------

        cursor.execute("""
            SELECT TABLE_NAME
            FROM ALL_TABLES
            WHERE OWNER = :schema
            ORDER BY TABLE_NAME
            FETCH FIRST 20 ROWS ONLY
        """, {
            "schema": Config.DB_SCHEMA
        })

        tables = cursor.fetchall()

        print("\nSample accessible tables:")

        if tables:
            for index, table in enumerate(tables, start=1):
                print(f"{index}. {table[0]}")
        else:
            print("No accessible tables found.")

        print("\n" + "=" * 60)
        print("ORACLE CONNECTION TEST COMPLETED")
        print("=" * 60)

        return True

    except oracledb.Error as error:

        print("\n[FAILED] Oracle Database connection or query.")

        print(f"Oracle Error: {error}")

        return False

    except Exception as error:

        print("\n[FAILED] Unexpected error.")
        print(f"Error: {error}")

        return False

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()
            print("\nDatabase connection closed.")


def main():

    print("\nDATA BULK APEX APP - ORACLE CONNECTION TEST")

    print("=" * 60)

    # Check configuration
    if not Config.DB_USER or not Config.DB_PASSWORD:
        print("[FAILED] DB_USER or DB_PASSWORD is missing in .env")
        sys.exit(1)

    if not Config.ORACLE_CLIENT_LIB_DIR:
        print("[FAILED] ORACLE_CLIENT_LIB_DIR is missing in .env")
        sys.exit(1)

    # Initialize Thick Mode first
    if not initialize_oracle_client():
        sys.exit(1)

    # Connect and test
    if not test_oracle_connection():
        sys.exit(1)

    print("\n[SUCCESS] ALL TESTS PASSED!")


if __name__ == "__main__":
    main()
