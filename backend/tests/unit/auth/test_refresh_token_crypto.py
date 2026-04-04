from app.services.auth_service.services.refresh_token_crypto import hash_refresh_token, new_refresh_token_value


def test_hash_refresh_token_stable_per_input():
    assert hash_refresh_token("same") == hash_refresh_token("same")
    assert hash_refresh_token("a") != hash_refresh_token("b")


def test_new_refresh_token_value_is_unique_across_calls():
    a = new_refresh_token_value()
    b = new_refresh_token_value()
    assert a != b
    assert len(a) > 20
