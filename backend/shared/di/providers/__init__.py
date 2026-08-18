from backend.shared.di.providers.app import AppProvider, SessionProvider

ALL_PROVIDERS = (AppProvider(), SessionProvider())

__all__ = ["ALL_PROVIDERS", "AppProvider", "SessionProvider"]
