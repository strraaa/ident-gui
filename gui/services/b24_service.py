"""
Обращения к порталу Битрикс24 из GUI: проверка вебхука и загрузка справочников.

Клиент создаётся на каждую операцию заново — конфигурация могла измениться
в соседней вкладке, а держать долгоживущее соединение здесь незачем.
"""

from typing import Dict, List, Optional

from src.bitrix.api_client import Bitrix24Client

from .config_service import ConfigService


class B24Service:
    """Справочники портала и проверка подключения"""

    def __init__(self, config: ConfigService):
        self.config = config

    def _client(self, webhook_url: Optional[str] = None) -> Bitrix24Client:
        url = webhook_url or self.config.webhook_url()
        if not url:
            raise RuntimeError(
                'Не задан адрес вебхука. Заполните его на вкладке «Подключения».'
            )

        return Bitrix24Client(
            webhook_url=url,
            request_timeout=self.config.get_int('bitrix', 'request_timeout', 30),
            max_retries=2  # в интерфейсе долгие повторы не нужны — оператор ждёт ответа
        )

    def test_connection(self, webhook_url: Optional[str] = None) -> str:
        """Проверяет вебхук. Возвращает текст для показа оператору."""
        client = self._client(webhook_url)
        client.test_connection()
        return 'Подключение к Битрикс24 работает'

    def load_user_fields(self, entity: str) -> List[Dict[str, str]]:
        """Пользовательские поля сущности ('deal', 'contact', 'lead')"""
        return self._client().get_user_fields(entity)

    def load_all_user_fields(self) -> Dict[str, List[Dict[str, str]]]:
        """Поля всех сущностей одним походом на портал"""
        client = self._client()
        return {
            'deal': client.get_user_fields('deal'),
            'contact': client.get_user_fields('contact'),
            'lead': client.get_user_fields('lead'),
        }

    def load_categories(self) -> List[Dict[str, str]]:
        """Воронки сделок"""
        return self._client().get_deal_categories()

    def load_stages(self, category_id: int) -> List[Dict[str, str]]:
        """Стадии указанной воронки"""
        return self._client().get_deal_stages(category_id)

    def load_stage_environment(self, category_id: int) -> Dict[str, List[Dict[str, str]]]:
        """Воронки, стадии выбранной воронки и статусы лидов — одним запросом набора"""
        client = self._client()
        return {
            'categories': client.get_deal_categories(),
            'stages': client.get_deal_stages(category_id),
            'lead_statuses': client.get_lead_statuses(),
        }


class DatabaseService:
    """Проверка подключения к SQL Server базы Ident"""

    def __init__(self, config: ConfigService):
        self.config = config

    def test_connection(self) -> str:
        """
        Пробует подключиться к БД с текущими настройками.

        Импорт коннектора отложен: он тянет pyodbc, которого может не быть
        на машине, где GUI используется только для просмотра настроек.
        """
        from src.database.ident_connector_v2 import IdentConnector

        if not self.config.manager:
            raise RuntimeError('Конфигурация не загружена')

        db_config = self.config.manager.get_database_config()

        connector = IdentConnector(
            server=db_config['server'],
            database=db_config['database'],
            username=db_config['username'],
            password=db_config['password'],
            port=db_config['port'],
            connection_timeout=db_config['connection_timeout'],
            query_timeout=db_config['query_timeout'],
            pool_size=1
        )

        try:
            connector.test_connection()
            return f"Подключение к БД {db_config['database']} работает"
        finally:
            try:
                connector.close()
            except Exception:
                pass
