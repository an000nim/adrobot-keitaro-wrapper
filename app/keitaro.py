import httpx
from fastapi import HTTPException


class Keitaro:
    def __init__(self, settings):
        self.settings = settings

    def request(self, method, path, **kwargs):
        key = self.settings.keitaro_api_key.get_secret_value()
        if not key:
            raise HTTPException(503, "Заполните KEITARO_API_KEY в .env и перезапустите сервер")
        try:
            with httpx.Client(
                base_url=self.settings.keitaro_url + "/admin_api/v1/",
                headers={"Api-Key": key}, timeout=30,
            ) as client:
                response = client.request(method, path, **kwargs)
        except httpx.RequestError:
            raise HTTPException(
                502, "Keitaro недоступен. При ошибке сохранения обновите данные перед повтором."
            ) from None
        if not response.is_success:
            # Upstream responses can contain configuration and secrets.
            raise HTTPException(502, f"Keitaro отклонил запрос, HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise HTTPException(502, "Keitaro вернул ответ в неожиданном формате") from None

    def campaigns(self):
        campaigns = []
        offset = 0
        while True:
            page = self.request("GET", "campaigns", params={"offset": offset, "limit": 100})
            campaigns.extend(page)
            if len(page) < 100:
                return campaigns
            offset += len(page)

    def campaign(self, campaign_id):
        return self.request("GET", f"campaigns/{campaign_id}")

    def streams(self, campaign_id):
        return self.request("GET", f"campaigns/{campaign_id}/streams")
