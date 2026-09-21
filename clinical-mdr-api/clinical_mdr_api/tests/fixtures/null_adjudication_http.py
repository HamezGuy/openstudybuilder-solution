"""Install native exception handlers without importing application startup."""

from common.exception_handlers import register_exception_handlers


def install_native_exception_handlers(app):
    register_exception_handlers(app, value_error=False)
