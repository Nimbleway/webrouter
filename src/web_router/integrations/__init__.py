"""Adapters that put web-router's methods in front of an agent framework.

Each lives in its own module and imports its framework there, so importing
this package costs nothing: ``from web_router.integrations.langchain import
WebRouterSearch`` needs the ``langchain`` extra, and nothing else does.
"""
