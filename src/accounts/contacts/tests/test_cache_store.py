"""Unit tests for the fail-open contact cache."""

from unittest.mock import Mock, patch

from redis.exceptions import RedisError

from contacts.cache_store import ContactsCache


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
