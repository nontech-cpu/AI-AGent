"""
Saptic AI Frontier Services Hub
Embedding, Cloudflare R2 Storage, MCP Client, ElevenLabs Audio Engine,
Relationship Knowledge Graph, Subscription Watchdog, Health Radar, and Shared Bubbles.
"""
from services.embeddings import embed_text, embed_query, embed_batch
from services.storage import upload_asset, process_and_ingest_asset
from services.mcp_client import MCPClient, discover_and_cache_mcp_tools
from services.elevenlabs_service import synthesize_speech, generate_morning_audio_briefing
from services.relationship_service import (
    add_or_update_relationship,
    upsert_relationship,
    get_user_relationships,
    list_relationships,
    resolve_relationship_in_text,
    get_upcoming_birthdays,
    format_relationships_context,
)
from services.subscription_service import (
    upsert_subscription,
    get_user_subscriptions,
    get_active_subscriptions,
    check_expiring_subscriptions,
)
from services.health_radar import compute_memory_health_score, format_health_radar_telegram_message
from services.shared_bubble_service import create_bubble_share_invite, join_shared_bubble, get_shared_bubbles_for_user
