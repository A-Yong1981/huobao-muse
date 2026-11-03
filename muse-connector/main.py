"""Scoped MCP connector for Muse to read and update Huobao Drama projects.

Muse performs generation. This service only exposes allowlisted project data,
uploads generated media, and links the returned Huobao storage paths to records.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
from pathlib import PurePosixPath
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


API_BASE = os.getenv("HUOBAO_API_BASE", "http://app:5679/api/v1").rstrip("/")
BRIDGE_TOKEN = os.getenv("MUSE_BRIDGE_TOKEN", "")
ALLOWED_DRAMA_IDS = frozenset(
    int(item.strip())
    for item in os.getenv("ALLOWED_DRAMA_IDS", "").split(",")
    if item.strip().isdigit()
)
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024

mcp = FastMCP("Huobao Drama Muse Connector", stateless_http=True)


class HuobaoError(RuntimeError):
    pass


async def request(method: str, path: str, *, json_body: Any = None, files: Any = None) -> Any:
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=600.0)) as client:
        try:
            response = await client.request(
                method, f"{API_BASE}{path}", json=json_body, files=files
            )
        except httpx.HTTPError as exc:
            raise HuobaoError("Huobao API is unreachable") from exc
    if response.is_error:
        raise HuobaoError(f"Huobao API returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise HuobaoError("Huobao API returned invalid JSON") from exc
    if isinstance(payload, dict) and payload.get("code", 200) >= 400:
        raise HuobaoError(str(payload.get("message") or "Huobao API rejected the request"))
    return payload.get("data", payload) if isinstance(payload, dict) else payload


async def assert_drama_allowed(drama_id: int) -> dict[str, Any]:
    if not ALLOWED_DRAMA_IDS or drama_id not in ALLOWED_DRAMA_IDS:
        raise HuobaoError("Drama is not allowlisted; configure ALLOWED_DRAMA_IDS")
    drama = await request("GET", f"/dramas/{drama_id}")
    if not isinstance(drama, dict) or int(drama.get("id", 0)) != drama_id:
        raise HuobaoError("Drama not found")
    return drama


async def assert_episode_allowed(episode_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    for drama_id in sorted(ALLOWED_DRAMA_IDS):
        try:
            drama = await assert_drama_allowed(drama_id)
        except HuobaoError:
            continue
        for episode in drama.get("episodes", []):
            if int(episode.get("id", 0)) == episode_id:
                return drama, episode
    raise HuobaoError("Episode is not in an allowlisted drama")


async def assert_storyboard_allowed(storyboard_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    for drama_id in sorted(ALLOWED_DRAMA_IDS):
        try:
            drama = await assert_drama_allowed(drama_id)
        except HuobaoError:
            continue
        for episode in drama.get("episodes", []):
            episode_id = int(episode.get("id", 0))
            storyboards = await request("GET", f"/episodes/{episode_id}/storyboards")
            for storyboard in storyboards if isinstance(storyboards, list) else []:
                if int(storyboard.get("id", 0)) == storyboard_id:
                    return drama, storyboard
    raise HuobaoError("Storyboard is not in an allowlisted drama")


@mcp.tool()
async def list_dramas() -> list[dict[str, Any]]:
    """List only dramas configured in ALLOWED_DRAMA_IDS."""
    if not ALLOWED_DRAMA_IDS:
        return []
    result = []
    for drama_id in sorted(ALLOWED_DRAMA_IDS):
        try:
            drama = await assert_drama_allowed(drama_id)
            result.append({key: drama.get(key) for key in ("id", "title", "description", "status")})
        except HuobaoError:
            continue
    return result


@mcp.tool()
async def get_drama(drama_id: int) -> dict[str, Any]:
    """Read one allowlisted drama and its related episodes, characters, scenes, and props."""
    return await assert_drama_allowed(drama_id)


@mcp.tool()
async def get_episode(episode_id: int) -> dict[str, Any]:
    """Read the script and metadata for an episode in an allowlisted drama."""
    _, episode = await assert_episode_allowed(episode_id)
    return episode


@mcp.tool()
async def update_episode_script(episode_id: int, script_content: str) -> dict[str, Any]:
    """Save Muse-authored script text to an allowlisted episode."""
    await assert_episode_allowed(episode_id)
    if len(script_content) > 1_000_000:
        raise HuobaoError("Script exceeds the 1 MB limit")
    return await request("PUT", f"/episodes/{episode_id}", json_body={"script_content": script_content})


@mcp.tool()
async def list_storyboards(episode_id: int) -> list[dict[str, Any]]:
    """Read all storyboards for an allowlisted episode."""
    await assert_episode_allowed(episode_id)
    result = await request("GET", f"/episodes/{episode_id}/storyboards")
    if not isinstance(result, list):
        raise HuobaoError("Unexpected storyboard response")
    return result


@mcp.tool()
async def update_storyboard(storyboard_id: int, fields_json: str) -> dict[str, Any]:
    """Update an allowlisted storyboard's prompts or descriptive fields using JSON."""
    await assert_storyboard_allowed(storyboard_id)
    try:
        fields = json.loads(fields_json)
    except json.JSONDecodeError as exc:
        raise HuobaoError("fields_json must be valid JSON") from exc
    allowed = {
        "title", "description", "shot_type", "angle", "movement", "duration",
        "video_prompt", "image_prompt", "scene_id", "location", "time",
        "atmosphere", "result", "bgm_prompt", "sound_effect",
    }
    if not isinstance(fields, dict) or not fields or set(fields) - allowed:
        raise HuobaoError("Only documented storyboard text and description fields can be updated")
    await request("PUT", f"/storyboards/{storyboard_id}", json_body=fields)
    return {"storyboard_id": storyboard_id, "updated_fields": sorted(fields)}


@mcp.tool()
async def upload_storyboard_media(
    storyboard_id: int,
    media_kind: str,
    filename: str,
    base64_data: str,
) -> dict[str, Any]:
    """Upload Muse-generated image/video bytes and attach them to an existing storyboard.

    media_kind must be storyboard_image, first_frame, last_frame, or video.
    Pass file bytes encoded as standard base64. Images are capped at 30 MiB and
    videos at 50 MiB. This writes to Huobao's normal static storage and updates
    the storyboard record with the resulting relative path.
    """
    await assert_storyboard_allowed(storyboard_id)
    fields = {
        "storyboard_image": ("image", "composed_image", MAX_IMAGE_BYTES),
        "first_frame": ("image", "first_frame_image", MAX_IMAGE_BYTES),
        "last_frame": ("image", "last_frame_image", MAX_IMAGE_BYTES),
        "video": ("video", "video_url", MAX_VIDEO_BYTES),
    }
    if media_kind not in fields:
        raise HuobaoError("media_kind must be storyboard_image, first_frame, last_frame, or video")
    if not filename or filename.rsplit("/", 1)[-1] in {".", ".."}:
        raise HuobaoError("filename is required")
    safe_name = PurePosixPath(filename.replace("\\", "/")).name
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    video_exts = {".mp4", ".mov", ".webm", ".m4v"}
    allowed_exts = video_exts if media_kind == "video" else image_exts
    if PurePosixPath(safe_name).suffix.lower() not in allowed_exts:
        raise HuobaoError(f"Unsupported file extension; allowed: {', '.join(sorted(allowed_exts))}")
    try:
        media = base64.b64decode(base64_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HuobaoError("base64_data is invalid") from exc
    upload_type, field_name, size_limit = fields[media_kind]
    if not media or len(media) > size_limit:
        raise HuobaoError(f"File must be between 1 byte and {size_limit // (1024 * 1024)} MiB")

    uploaded = await request(
        "POST",
        f"/upload/{upload_type}",
        files={"file": (safe_name, media, "application/octet-stream")},
    )
    relative_path = uploaded.get("path") if isinstance(uploaded, dict) else None
    if not isinstance(relative_path, str) or not relative_path.startswith("static/"):
        raise HuobaoError("Huobao upload did not return a valid storage path")
    await request("PUT", f"/storyboards/{storyboard_id}", json_body={field_name: relative_path})
    return {"storyboard_id": storyboard_id, "media_kind": media_kind, "path": relative_path}


@mcp.tool()
async def upload_drama_asset(
    drama_id: int,
    entity_type: str,
    entity_id: int,
    filename: str,
    base64_data: str,
) -> dict[str, Any]:
    """Upload a Muse-generated character, scene, or prop image and attach it."""
    drama = await assert_drama_allowed(drama_id)
    entity_paths = {
        "character": ("characters", "/characters"),
        "scene": ("scenes", "/scenes"),
        "prop": ("props", "/props"),
    }
    if entity_type not in entity_paths:
        raise HuobaoError("entity_type must be character, scene, or prop")
    collection, route = entity_paths[entity_type]
    entity = next((item for item in drama.get(collection, []) if int(item.get("id", 0)) == entity_id), None)
    if entity is None:
        raise HuobaoError("Entity does not belong to the allowlisted drama")
    if not filename or filename.rsplit("/", 1)[-1] in {".", ".."}:
        raise HuobaoError("filename is required")
    safe_name = PurePosixPath(filename.replace("\\", "/")).name
    if PurePosixPath(safe_name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise HuobaoError("Character/scene/prop images must be PNG, JPEG, WebP, or GIF")
    try:
        media = base64.b64decode(base64_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HuobaoError("base64_data is invalid") from exc
    if not media or len(media) > MAX_IMAGE_BYTES:
        raise HuobaoError("Image must be between 1 byte and 30 MiB")
    uploaded = await request(
        "POST", "/upload/image",
        files={"file": (safe_name, media, "application/octet-stream")},
    )
    relative_path = uploaded.get("path") if isinstance(uploaded, dict) else None
    if not isinstance(relative_path, str) or not relative_path.startswith("static/"):
        raise HuobaoError("Huobao upload did not return a valid storage path")
    await request("PUT", f"{route}/{entity_id}", json_body={"image_url": relative_path, "local_path": relative_path})
    return {"drama_id": drama_id, "entity_type": entity_type, "entity_id": entity_id, "path": relative_path}


def authorized_app(inner):
    async def app(scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/health":
            payload = json.dumps({
                "status": "ok" if BRIDGE_TOKEN and ALLOWED_DRAMA_IDS else "needs_configuration",
                "token_configured": bool(BRIDGE_TOKEN),
                "allowlisted_drama_count": len(ALLOWED_DRAMA_IDS),
            }).encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": payload})
            return
        if scope.get("type") == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode("latin1")
            supplied = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not BRIDGE_TOKEN or not hmac.compare_digest(supplied, BRIDGE_TOKEN):
                body = b'{"error":"unauthorized"}'
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await inner(scope, receive, send)
    return app


app = authorized_app(mcp.streamable_http_app())
