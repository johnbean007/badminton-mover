"""R2 and Supabase access for the worker. Credentials come from the Modal secrets
badminton-mover-r2 and badminton-mover-supabase; nothing here ever prints them."""
from __future__ import annotations

import os
from functools import cache

import boto3
import httpx


@cache
def _r2():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID'].strip()}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        region_name="auto",
    )


def _bucket() -> str:
    return os.environ["R2_BUCKET"].strip()


def download(key: str, path: str) -> None:
    _r2().download_file(_bucket(), key, path)


def upload(path: str, key: str, content_type: str) -> None:
    _r2().upload_file(path, _bucket(), key, ExtraArgs={"ContentType": content_type})


def put_bytes(data: bytes, key: str, content_type: str) -> None:
    _r2().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)


def delete(keys: list[str]) -> None:
    if keys:
        _r2().delete_objects(Bucket=_bucket(), Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})


@cache
def _db() -> httpx.Client:
    key = os.environ["SUPABASE_SECRET_KEY"].strip()
    return httpx.Client(
        base_url=os.environ["SUPABASE_URL"].strip().rstrip("/") + "/rest/v1",
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=30,
    )


def select(table: str, **filters: str) -> list[dict]:
    r = _db().get(f"/{table}", params={"select": "*", **filters})
    r.raise_for_status()
    return r.json()


def update(table: str, values: dict, **filters: str) -> list[dict]:
    r = _db().patch(f"/{table}", params=filters, json=values, headers={"Prefer": "return=representation"})
    r.raise_for_status()
    return r.json()


def insert(table: str, rows: list[dict]) -> None:
    if rows:
        _db().post(f"/{table}", json=rows).raise_for_status()


def remove(table: str, **filters: str) -> list[dict]:
    r = _db().delete(f"/{table}", params=filters, headers={"Prefer": "return=representation"})
    r.raise_for_status()
    return r.json()
