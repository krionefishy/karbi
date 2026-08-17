from backend.shared.di.providers.app import AppProvider, SessionProvider, WorkerProvider

ALL_PROVIDERS = (AppProvider(), SessionProvider())
WORKER_PROVIDERS = (WorkerProvider(), SessionProvider())

__all__ = ["ALL_PROVIDERS", "WORKER_PROVIDERS", "AppProvider", "SessionProvider", "WorkerProvider"]
