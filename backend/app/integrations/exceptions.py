"""Errores comunes para todos los proveedores externos.

Los adapters lanzan estas excepciones; el resto de la app nunca ve errores de httpx.
"""


class ProviderError(Exception):
    """Error genérico de un proveedor externo."""

    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        self.provider = provider
        self.message = message
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class ProviderNotConfiguredError(ProviderError):
    """Falta la API key u otra configuración necesaria."""


class ProviderTimeoutError(ProviderError):
    """El proveedor tardó demasiado en responder."""


class ProviderAuthError(ProviderError):
    """API key inválida, caducada o sin permisos."""


class ProviderRateLimitError(ProviderError):
    """Se superó el límite de peticiones del plan."""


class ProviderResponseError(ProviderError):
    """Respuesta HTTP de error o respuesta con formato inesperado."""
