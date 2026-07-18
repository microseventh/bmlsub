"""Entry-point-only credential resolution for external release services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from ..credentials.keychain import SecretStore
from ..credentials.manifest import load_secure_json
from ..credentials.service import (
    AnibtCredentials, CredentialService, QBittorrentCredentials, R2Credentials,
)


def resolve_r2_credentials(*, account_id_env: str = "R2_ACCOUNT_ID",
                           access_key_env: str = "R2_ACCESS_KEY_ID",
                           secret_key_env: str = "R2_SECRET_ACCESS_KEY",
                           endpoint_env: str = "R2_ENDPOINT",
                           config_path: Path | str | None = None,
                           manifest_path: Path | str | None = None,
                           profile_alias: str | None = None,
                           secret_store: SecretStore | None = None,
                           environment: Mapping[str, str] | None = None) -> R2Credentials:
    if manifest_path is not None or profile_alias is not None:
        if manifest_path is None or profile_alias is None:
            raise ValueError("R2 credential manifest and profile must be provided together")
        if config_path is not None:
            raise ValueError("R2 credential manifest and file are mutually exclusive")
        return CredentialService(
            manifest_path=manifest_path, secret_store=secret_store,
        ).resolve_r2(profile_alias)
    env = environment or os.environ
    file_path = Path(config_path).expanduser() if config_path is not None else None
    file_data = load_secure_json(file_path) if file_path is not None else {}
    account_id = env.get(account_id_env, "").strip() or str(file_data.get("account_id", "")).strip()
    access_key_id = env.get(access_key_env, "").strip() or str(file_data.get("access_key_id", "")).strip()
    secret_access_key = env.get(secret_key_env, "") or str(file_data.get("secret_access_key", ""))
    endpoint = env.get(endpoint_env, "").strip() or str(file_data.get("endpoint", "")).strip()
    if not account_id or not access_key_id or not secret_access_key:
        raise ValueError("R2 credential environment references are incomplete")
    if not endpoint:
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    if not endpoint.startswith("https://"):
        raise ValueError("R2 endpoint must use HTTPS")
    return R2Credentials(
        account_id=account_id, access_key_id=access_key_id,
        secret_access_key=secret_access_key, endpoint_url=endpoint,
        reference=(
            f"file:{file_path.name}" if file_path is not None
            else f"env:{account_id_env},{access_key_env},{secret_key_env},{endpoint_env}"
        ),
    )


def resolve_qbittorrent_credentials(*, username_env: str = "QB_USERNAME",
                                    password_env: str = "QB_PASSWORD",
                                    config_path: Path | str | None = None,
                                    manifest_path: Path | str | None = None,
                                    profile_alias: str | None = None,
                                    secret_store: SecretStore | None = None,
                                    environment: Mapping[str, str] | None = None) -> QBittorrentCredentials:
    if manifest_path is not None or profile_alias is not None:
        if manifest_path is None or profile_alias is None:
            raise ValueError("qBittorrent credential manifest and profile must be provided together")
        if config_path is not None:
            raise ValueError("qBittorrent credential manifest and file are mutually exclusive")
        return CredentialService(
            manifest_path=manifest_path, secret_store=secret_store,
        ).resolve_qbittorrent(profile_alias)
    env = environment or os.environ
    file_path = Path(config_path).expanduser() if config_path is not None else None
    file_data = load_secure_json(file_path) if file_path is not None else {}
    username = env.get(username_env, "").strip() or str(file_data.get("username", "")).strip()
    password = env.get(password_env, "") or str(file_data.get("password", ""))
    if not username or not password:
        raise ValueError("qBittorrent credential environment references are incomplete")
    return QBittorrentCredentials(
        username=username, password=password,
        reference=(
            f"file:{file_path.name}" if file_path is not None
            else f"env:{username_env},{password_env}"
        ),
    )


def resolve_anibt_credentials(*, token: str | None = None,
                               token_env: str = "ANIBT_TOKEN",
                               config_path: Path | str | None = None,
                               manifest_path: Path | str | None = None,
                               profile_alias: str | None = None,
                               secret_store: SecretStore | None = None,
                               api_url: str | None = None,
                               environment: Mapping[str, str] | None = None) -> AnibtCredentials:
    if manifest_path is not None or profile_alias is not None:
        if manifest_path is None or profile_alias is None:
            raise ValueError("Anibt credential manifest and profile must be provided together")
        if config_path is not None or token is not None:
            raise ValueError("Anibt credential manifest is mutually exclusive with token and file")
        return CredentialService(
            manifest_path=manifest_path, secret_store=secret_store,
        ).resolve_anibt(profile_alias, api_url=api_url)
    env = environment or os.environ
    file_path = Path(config_path).expanduser() if config_path is not None else None
    file_data = load_secure_json(file_path) if file_path is not None else {}
    resolved_token = (
        token
        or env.get(token_env, "").strip()
        or str(file_data.get("token", "")).strip()
    )
    resolved_api_url = (
        api_url
        or str(file_data.get("api_url", "")).strip()
        or "https://anibt.net/api/releases/publish"
    )
    if not resolved_token:
        raise ValueError("anibt token is not configured")
    if not resolved_api_url.startswith("https://"):
        raise ValueError("anibt API URL must use HTTPS")
    return AnibtCredentials(
        token=resolved_token,
        api_url=resolved_api_url,
        reference=(
            f"file:{file_path.name}" if file_path is not None
            else f"env:{token_env}"
        ),
    )
