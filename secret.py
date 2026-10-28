import psycopg2
from globals import GlobalConfig

def create_connection(database, user):
    return psycopg2.connect(
        host=GlobalConfig.host,
        database=database,
        user=user,
        password=GlobalConfig.password
    )

def get_moi_sklad_api_key_db():
    """Ключи Мой склад"""
    conn = create_connection("secrets_db", "gen_user")
    cur = conn.cursor()
    cur.execute("""
        SELECT moi_sklad_api_key
        FROM moi_sklad_api_key_tb
    """)
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row:
        return row[0]
    else:
        raise ValueError("Error.")
    
def get_lamoda_keys_from_db():
    """Ключи Lamoda"""
    conn = create_connection("secrets_db", "gen_user")
    
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT fbs_lamoda_client_id, 
                   fbs_lamoda_client_secret, 
                   fbo_lamoda_client_id, 
                   fbo_lamoda_client_secret 
            FROM lamoda_keys_tb 
            LIMIT 1
        """)
        
        result = cursor.fetchone()
        
        if result:
            return {
                'fbs_client_id': result[0],
                'fbs_client_secret': result[1],
                'fbo_client_id': result[2],
                'fbo_client_secret': result[3]
            }
        else:
            raise Exception("Ключи не найдены в базе данных")
            
    finally:
        cursor.close()
        conn.close()

moi_sklad_api_key = get_moi_sklad_api_key_db()
