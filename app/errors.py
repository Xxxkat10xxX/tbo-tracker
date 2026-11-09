class DomainError(Exception):
    """Нарушение бизнес-правила; отдаётся клиенту как 4xx с понятным текстом."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFound(DomainError):
    def __init__(self, message: str = "Объект не найден"):
        super().__init__(message, 404)


def require_demo_mode():
    """Сброс базы, сценарии и демо-журналы — только в демонстрационном режиме (TBO_DEMO_MODE=1)."""
    from .config import DEMO_MODE
    if not DEMO_MODE:
        raise NotFound("Демонстрационный режим выключен (TBO_DEMO_MODE=0)")
