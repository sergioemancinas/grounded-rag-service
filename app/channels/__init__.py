"""Channel adapters: everything that turns a chat platform into an AskFn call.

The core service (app/main.py) knows nothing about Slack, Teams, Discord, or
any other surface. Each adapter lives in one module here, exports
``create_router(ask, settings, feedback=None)``, and is mounted only when its
credentials are configured. Deleting an adapter is a one-file change that
touches no core module.
"""
