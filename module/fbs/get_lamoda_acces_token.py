import gzip
import http.client
from io import BytesIO
import json
import os
from dotenv import load_dotenv
from secret import get_lamoda_keys_from_db
from globals import get_logger

load_dotenv()

logger = get_logger("get_lamoda_acces_token", "module/fbs/get_lamoda_acces_token.log", logs_dir="logs")

seller_id = os.getenv("LAMODA_SELLER_ID")

access_token = None

async def get_access_token():
    global access_token

    try:
        keys = get_lamoda_keys_from_db()
        conn = http.client.HTTPSConnection("public-api-seller.lamoda.ru")

        payload = json.dumps({
            "grant_type": "client_credentials",
            "client_id": keys["fbs_client_id"],
            "client_secret": keys["fbs_client_secret"]
        })

        headers = {
        'Content-Type': 'application/json'
        }

        conn.request("POST", "/api/v2/auth-token", payload, headers)
        res = conn.getresponse()
        data = res.read()
        if res.getheader('Content-Encoding') == 'gzip':
            buf = BytesIO(data)
            data = gzip.decompress(buf.read())

        json_string = data.decode("utf-8")
        json_data = json.loads(json_string)
        access_token = json_data.get("access_token")

        token_preview = f"{access_token[:20]}...{access_token[-20:]}"
        logger.info(f"Получен новый токен Lamoda : {token_preview}")

        return access_token

    except Exception as e:
        logger.error(f"Ошибка получения токена: {e}")
        return None