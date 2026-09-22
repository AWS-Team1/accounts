"""Unit tests for the fail-open contact cache."""

from unittest.mock import Mock, patch

from redis.exceptions import RedisClusterException, RedisError

from contacts.cache_store import ContactsCache, create_valkey_client


@patch.dict("contacts.cache_store.os.environ", {
    "VALKEY_HOST": "unreachable.invalid",
    "VALKEY_CLUSTER_MODE": "true",
})
@patch("contacts.cache_store.RedisCluster")
def test_create_client_treats_cluster_initialization_error_as_unavailable(
        redis_cluster):
    redis_cluster.side_effect = RedisClusterException("unavailable")

    assert create_valkey_client() is None


def test_get_returns_cached_contacts():
    client = Mock()
    client.get.return_value = '[{"label": "Alice"}]'
    cache = ContactsCache(client)

    assert cache.get("testuser") == [{"label": "Alice"}]


def test_get_treats_valkey_error_as_cache_miss():
    client = Mock()
    client.get.side_effect = RedisError("unavailable")
    cache = ContactsCache(client)

    assert cache.get("testuser") is None


@patch("contacts.cache_store.random.randint", return_value=7)
def test_set_applies_ttl_jitter(mock_randint):
    client = Mock()
    cache = ContactsCache(client, ttl_seconds=600, jitter_seconds=60)

    cache.set("testuser", [{"label": "Alice"}])

    client.set.assert_called_once()
    assert client.set.call_args.kwargs["ex"] == 607
    mock_randint.assert_called_once_with(0, 60)
