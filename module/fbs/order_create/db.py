# db.py
import psycopg2
from globals import GlobalConfig, get_logger

logger = get_logger("db", "module/fbs/order_create/db.log", logs_dir="logs")

def create_connection():
    return psycopg2.connect(
        host=GlobalConfig.host,
        database="lamoda_db",
        user=GlobalConfig.user,
        password=GlobalConfig.password
    )

def create_order_log_db(id_number: str, date: str):
    """Добавление заказа в БД"""
    try:
        conn = create_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO transfer_order_to_ms_logs (id_number, date)
            VALUES (%s, %s)
            ON CONFLICT (id_number) DO NOTHING
        """, (id_number, date,))

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"Ошибка записи лога заказа {id_number}: {e}")

def update_order_stage_db(id_number: str, stage: str, status: str):
    """Обновление статусов в БД"""
    try:
        conn = create_connection()
        cursor = conn.cursor()

        cursor.execute(
            f'UPDATE transfer_order_to_ms_logs SET "{stage}" = %s WHERE id_number = %s',
            (status, id_number)
        )

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"Ошибка записи этапа {stage} для заказа {id_number}: {e}")

def update_stage_7_db(id_number: str, details: str, status: str):
    """Товары из заказа"""
    try:
        conn = create_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE transfer_order_to_ms_logs
            SET "7_stage" = %s,
                "7_stage_status" = %s
            WHERE id_number = %s
        """, (details, status, id_number))

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"Ошибка записи этапа 7 для заказа {id_number}: {e}")

def update_ms_order_db(id_number: str, result: str):
    """Заказ в Мой Склад"""
    try:
        conn = create_connection()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE transfer_order_to_ms_logs
            SET ms_order = %s
            WHERE id_number = %s
        """, (result, id_number))

        conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        logger.error(f"Ошибка записи результата Lamoda для заказа {id_number}: {e}")
