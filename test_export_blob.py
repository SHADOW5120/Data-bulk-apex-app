
import os
import oracledb

from config import Config

# Initialize Oracle Thick Mode
if Config.ORACLE_CLIENT_LIB_DIR:
    oracledb.init_oracle_client(
        lib_dir=Config.ORACLE_CLIENT_LIB_DIR
    )

conn = oracledb.connect(
    user=Config.DB_USER,
    password=Config.DB_PASSWORD,
    dsn=Config.DB_DSN
)

cursor = conn.cursor()

ITEM_SPEC_ID = 438

os.makedirs("blob_test", exist_ok=True)

try:
    cursor.execute("""
        SELECT
            ITEM_IMAGE,
            ITEM_IMG_NAME,
            CIRCUIT_IMAGE,
            CIRCUIT_IMG_NAME
        FROM APPS.SDM_ITEM_SPEC_IMAGE
        WHERE ITEM_SPEC_ID = :id
    """, id=ITEM_SPEC_ID)

    row = cursor.fetchone()

    if not row:
        print("Record not found")
    else:
        for blob, filename in [
            (row[0], row[1]),
            (row[2], row[3])
        ]:
            if blob is None:
                print("BLOB is NULL:", filename)
                continue

            output_path = os.path.join(
                "blob_test",
                os.path.basename(filename)
            )

            with open(output_path, "wb") as f:
                f.write(blob.read())

            print("Exported:", output_path)

finally:
    cursor.close()
    conn.close()
