"""Fail-open Valkey cache for contact lists."""

import hashlib
import json
import os
import random

import redis
from redis.cluster import RedisCluster
from redis.exceptions import RedisClusterException, RedisError


def create_valkey_client():
    """Create a TLS-enabled standalone or cluster Valkey client."""
    common = {
        "decode_responses": True,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "health_check_interval": 30,
    }
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis.Redis.from_url(redis_url, **common)

    host = os.getenv("VALKEY_HOST")
    if not host:
        return None
    client_class = (RedisCluster if os.getenv(
        "VALKEY_CLUSTER_MODE", "true").lower() == "true" else redis.Redis)
    use_tls = os.getenv("VALKEY_TLS", "true").lower() == "true"
    try:
        return client_class(
            host=host,
            port=int(os.getenv("VALKEY_PORT", "6379")),
            username=os.getenv("VALKEY_USERNAME"),
            password=os.getenv("VALKEY_PASSWORD"),
            ssl=use_tls,
            ssl_cert_reqs="required" if use_tls else None,
            **common,
        )
    except (RedisError, RedisClusterException):
        return None


class ContactsCache:
    """Cache-aside contact storage; every Valkey error is a cache miss."""

    KEY_PREFIX = "boa:cache:v1:contacts:"

    def __init__(self, client, ttl_seconds=600, jitter_seconds=60, logger=None):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.jitter_seconds = jitter_seconds
        self.logger = logger

    @staticmethod
    def _key(username):
        digest = hashlib.sha256(username.lower().encode("utf-8")).hexdigest()
        return ContactsCache.KEY_PREFIX + "{" + digest[:32] + "}"

    def get(self, username):
        if self.client is None:
            return None
        try:
            value = self.client.get(self._key(username))
            return None if value is None else json.loads(value)
        except (RedisError, TypeError, ValueError) as err:
            if self.logger:
                self.logger.warning("Contact cache read failed; using DB: %s", err)
            return None

    def set(self, username, contacts):
        if self.client is None:
            return
        ttl = self.ttl_seconds + random.randint(0, self.jitter_seconds)
        try:
            self.client.set(self._key(username), json.dumps(contacts), ex=ttl)
        except RedisError as err:
            if self.logger:
                self.logger.warning("Contact cache write failed: %s", err)

    def invalidate(self, username):
        if self.client is None:
            return
        try:
            self.client.delete(self._key(username))
        except RedisError as err:
            if self.logger:
                self.logger.warning("Contact cache invalidation failed: %s", err)
