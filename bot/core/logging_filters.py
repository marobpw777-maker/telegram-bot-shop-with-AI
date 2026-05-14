# core/logging_filters.py
"""
Фильтры для безопасного логирования.
Автоматически скрывают секреты в логах.
"""
import logging
import re
from typing import List, Tuple


class SecretRedactionFilter(logging.Filter):
    """
    Фильтр для автоматического скрытия секретов в логах.
    
    Заменяет чувствительные данные на [REDACTED]:
    - Токены бота
    - Секретные ключи YooKassa
    - Пароли БД
    - API ключи
    """
    
    # Паттерны для поиска секретов (regex, replacement)
    SENSITIVE_PATTERNS: List[Tuple[str, str]] = [
        # Токены и секреты
        (r'(BOT_TOKEN[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        (r'(YOOKASSA_SECRET_KEY[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        (r'(YOOKASSA_WEBHOOK_TOKEN[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        (r'(FORWARD_SECRET[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        (r'(PROVIDER_TOKEN[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        
        # Пароли БД
        (r'(MYSQL_PASSWORD[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        (r'(DB_PASS[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        (r'(YOOKASSA_RELAY_DB_PASS[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        
        # SMTP пароли
        (r'(SMTP_PASS[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        
        # GigaChat ключи
        (r'(GIGACHAT_AUTH_KEY[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        
        # Yandex токены
        (r'(YANDEX.*?TOKEN[=:\s]+)[^\s]+', r'\1[REDACTED]'),
        
        # URL с токенами (?token=...)
        (r'(\?token=)[^\s&]+', r'\1[REDACTED]'),
        
        # Base64 строки (потенциальные ключи)
        (r'[A-Za-z0-9+/]{40,}={0,2}', '[REDACTED_BASE64]'),
    ]
    
    def __init__(self, name='', enabled=True):
        super().__init__(name)
        self.enabled = enabled
    
    def filter(self, record):
        """
        Фильтрация записи лога.
        
        Args:
            record: LogRecord объект
            
        Returns:
            True если запись должна быть залогирована
        """
        if not self.enabled:
            return True
        
        try:
            # Обрабатываем message
            if isinstance(record.msg, str):
                for pattern, replacement in self.SENSITIVE_PATTERNS:
                    record.msg = re.sub(pattern, replacement, record.msg)
            
            # Обрабатываем args (если есть format args)
            if record.args:
                new_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        for pattern, replacement in self.SENSITIVE_PATTERNS:
                            arg = re.sub(pattern, replacement, arg)
                    new_args.append(arg)
                record.args = tuple(new_args)
            
            # Обрабатываем exc_text (если есть exception)
            if hasattr(record, 'exc_text') and record.exc_text:
                for pattern, replacement in self.SENSITIVE_PATTERNS:
                    record.exc_text = re.sub(pattern, replacement, record.exc_text)
            
        except Exception as e:
            # Если фильтр упал - не блокируем логирование
            record.msg = f"[REDACTION_ERROR: {e}] {record.msg}"
        
        return True


class PIIFilter(logging.Filter):
    """
    Фильтр для защиты персональных данных (PII).
    
    Маскирует:
    - Номера телефонов
    - Email адреса
    - Номерa карт
    """
    
    PII_PATTERNS: List[Tuple[str, str]] = [
        # Телефоны (разные форматы)
        (r'(\+7\s*\(\d{3}\)\s*\d{3}-\d{2}-\d{2})', '[PHONE_REDACTED]'),
        (r'(\+7\s*\d{3}\s*\d{3}-\d{2}-\d{2})', '[PHONE_REDACTED]'),
        (r'(8\s*\(\d{3}\)\s*\d{3}-\d{2}-\d{2})', '[PHONE_REDACTED]'),
        
        # Email
        (r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL_REDACTED]'),
        
        # Номерa карт (16 цифр с пробелами или без)
        (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[CARD_REDACTED]'),
    ]
    
    def __init__(self, name='', enabled=False):
        """
        Args:
            enabled: По умолчанию False (т.к. может ломать логи для отладки)
        """
        super().__init__(name)
        self.enabled = enabled
    
    def filter(self, record):
        if not self.enabled:
            return True
        
        try:
            if isinstance(record.msg, str):
                for pattern, replacement in self.PII_PATTERNS:
                    record.msg = re.sub(pattern, replacement, record.msg)
            
            if record.args:
                new_args = []
                for arg in record.args:
                    if isinstance(arg, str):
                        for pattern, replacement in self.PII_PATTERNS:
                            arg = re.sub(pattern, replacement, arg)
                    new_args.append(arg)
                record.args = tuple(new_args)
                
        except Exception as e:
            record.msg = f"[PII_FILTER_ERROR: {e}] {record.msg}"
        
        return True


def setup_secure_logging(logger_name: str = None, enable_pii_filter: bool = False):
    """
    Настройка безопасного логирования с фильтрацией секретов.
    
    Args:
        logger_name: Имя логгера (или root если None)
        enable_pii_filter: Включить ли фильтрацию PII (телефоны, email)
        
    Example:
        >>> setup_secure_logging()
        >>> logger = logging.getLogger(__name__)
        >>> logger.info("Bot token: %s", BOT_TOKEN)  # В логе будет [REDACTED]
    """
    import logging
    
    # Получаем логгер
    if logger_name:
        logger = logging.getLogger(logger_name)
    else:
        logger = logging.getLogger()
    
    # Добавляем фильтр секретов
    secret_filter = SecretRedactionFilter(name='secret_redaction', enabled=True)
    logger.addFilter(secret_filter)
    
    # Опционально добавляем PII фильтр
    if enable_pii_filter:
        pii_filter = PIIFilter(name='pii_redaction', enabled=True)
        logger.addFilter(pii_filter)
    
    logger.debug(
        "Secure logging setup complete. "
        f"Secret filter: ENABLED, PII filter: {'ENABLED' if enable_pii_filter else 'DISABLED'}"
    )
    
    return logger
