#!/usr/bin/env python3
"""Route-level wrappers around app services to keep app.py slimmer."""

import api_service


def handle_config_get():
    return api_service.build_config_response()


def handle_config_post(body):
    return api_service.apply_config_updates(body)
