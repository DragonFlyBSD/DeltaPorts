"""HTTP route groups for the tracker FastAPI app.

Each module exposes ``register(app, ctx)`` which attaches its routes to the
passed app. ``server.create_app`` builds one ``RouteContext`` and calls each
group's ``register`` — so ``server.py`` is app assembly + wiring only, and the
route bodies live next to their peers.
"""
