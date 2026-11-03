# Muse MCP connector

The connector lets Muse perform model generation and write the results into Huobao Drama. It follows the ArcReel handoff pattern: Muse is the generation agent; Huobao remains the project, script, storyboard, and media store. Existing provider-based generation remains available.

## Tools exposed to Muse

- `list_dramas`, `get_drama`, `get_episode`, and `list_storyboards` read only dramas explicitly listed in `ALLOWED_DRAMA_IDS`.
- `update_episode_script` saves Muse-authored episode script text.
- `update_storyboard` updates storyboard prompts and descriptive fields.
- `upload_storyboard_media` uploads an image or video and links it to the selected storyboard field.
- `upload_drama_asset` uploads and links character, scene, or prop images.

The connector does not expose arbitrary HTTP requests, deletion, AI provider credentials, or unrestricted filesystem access. Image uploads are limited to 30 MiB; video uploads are limited to 50 MiB. Uploads go through Huobao's existing media routes and are stored under its normal persistent data volume.

## Build and start the integrated Compose project

1. Copy `.env.example` to `.env` if you have not already done so.
2. Set a unique long `MUSE_BRIDGE_TOKEN` and an explicit comma-separated `ALLOWED_DRAMA_IDS`, for example `12,13`. An empty allowlist denies every drama.
3. Build and start the full Compose project, including both Huobao Drama and the connector:

   ```sh
   docker compose up -d --build
   ```

4. After creating a drama, add its ID to `ALLOWED_DRAMA_IDS` in `.env`, then recreate the connector with `docker compose up -d --force-recreate muse-connector`. An empty allowlist denies all drama access.
5. Check `http://127.0.0.1:5680/health`. The health response reports configuration presence and allowlist count, never the token.
6. Add an MCP server to Muse using the connector URL and bearer token. For a same-host deployment, the URL is `http://127.0.0.1:5680/mcp`. For Muse on another machine, expose the connector through an HTTPS reverse proxy or the existing authenticated relay and use that HTTPS `/mcp` URL. Keep the Compose bind address on loopback when the proxy runs on the same host.

Example MCP connection settings (replace placeholders locally):

```json
{
  "url": "https://your-domain.example/huobao-muse/mcp",
  "headers": {
    "Authorization": "Bearer <MUSE_BRIDGE_TOKEN>"
  }
}
```

Do not commit the real token or put it in prompts or logs. Rotate it by changing `MUSE_BRIDGE_TOKEN` and recreating `muse-connector`.

## Workflow

1. Ask Muse to use `list_dramas` and read the target drama and episode.
2. Have Muse draft or revise the script, then call `update_episode_script` to save it.
3. Read the episode storyboards, revise their prompts/descriptions with `update_storyboard`, and generate media in Muse.
4. Upload the completed image/video with `upload_storyboard_media`, or upload character/scene/prop images with `upload_drama_asset`.
5. Re-read the episode in Huobao and review the saved text and attached media.

For manual stage approval, configure Muse's workflow instructions to stop after each production stage and wait for your approval before continuing. The connector provides the read/write tools; it does not enforce approval gates itself.

## Current integration boundary

This connector provides a Muse-driven content and media handoff. It does not make the Muse desktop app callable as a provider API, and it does not yet mirror Muse runs into Huobao's `sys_task` progress panel. Existing in-app text, image, and video provider tasks continue to use their configured API providers. A later lifecycle integration can add queued/claimed/completed Muse jobs if the project needs task-level progress and retry controls.
