"""Server configuration object"""

import os
from typing import Literal

from aiocache import caches
from pydantic import BaseModel, Field, validator

caches.set_config(
    {
        "default": {
            "cache": "aiocache.SimpleMemoryCache",
            "serializer": {"class": "aiocache.serializers.StringSerializer"},
        },
    }
)

LogLevel = Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class AyonConfig(BaseModel):
    """Server configuration"""

    # We only need to handle AYON_RUN_MAINTENANCE here,
    # as RUN_SERVER and RUN_SETUP are handled outside the server.
    run_maintenance: bool = Field(
        default=True,
        description="Run maintenance procedure in the background "
        "in the main server container. "
        "Set to false when scaling the server to multiple instances.",
    )

    http_listen_address: str = Field(
        default="0.0.0.0",
        description="An address the API server listens on",
    )

    http_listen_port: int = Field(
        default=5000,
        description="A port the API server listens on",
    )

    api_modules_dir: str = Field(
        default="api",
        description="Path to the directory containing the API modules.",
    )

    avatar_dir: str = Field(
        default="/storage/server/avatars",
        description="Path to the directory containing the user avatars.",
    )

    addons_dir: str = Field(
        default="/addons",
        description="Absolute path to the directory containing the addons.",
    )

    frontend_dir: str = Field(
        default="/frontend",
        description="Path to the directory containing the frontend files.",
    )

    auth_pass_pepper: str = Field(
        default="supersecretpasswordpepper",
        description="A secret string used to salt the password hash.",
    )

    auth_pass_min_length: int = Field(
        default=8,
        description="Minimum password length.",
    )

    auth_pass_complex: bool = Field(
        default=True,
        description="Enforce using a complex password.",
    )

    redis_url: str = Field(
        default="redis://redis/",
        description="Connection string for Redis.",
        example="redis://user:password123@redis.example.com:6379",
    )

    redis_channel: str = Field(
        default="pype:c",
        description="Redis channel name for system messages",
    )

    redis_key_prefix: str | None = Field(
        default=None,
        description="Redis keys prefix",
    )

    postgres_url: str = Field(
        default="postgres://ayon:ayon@postgres/ayon",
        description="Connection string for Postgres.",
        example="postgres://user:password123@postgres.example.com:5432/ayon",
    )

    postgres_pool_size: int = Field(
        64,
        description="Postgres connection pool size",
        example=64,
    )

    postgres_pool_timeout: int = Field(
        20,
        description="Postgres connection pool timeout",
        example=20,
    )

    session_ttl: int = Field(
        default=72 * 3600,
        description="Session lifetime in seconds",
    )

    disable_check_session_ip: bool = Field(
        default=False,
        description="Skip checking session IP match real IP",
    )

    max_concurent_user_sessions: int | None = Field(
        default=None,
        description="Maximum number of concurrent user sessions",
    )

    max_failed_login_attempts: int = Field(
        default=10,
        description="Maximum number of failed login attempts",
    )

    failed_login_ban_time: int = Field(
        default=600,
        description="Interval in seconds to ban IP addresses "
        "with too many failed login attempts",
    )

    motd: str | None = Field(
        default=None,
        description="Message of the day",
        example="Welcome to Ayon!",
    )

    motd_path: str | None = Field(
        default="/storage/motd.md",
        description="Path to the MOTD file",
    )

    login_page_background: str | None = Field(
        default=None,
        description="Login page background image",
        example="https://example.com/background.jpg",
    )

    login_page_brand: str | None = Field(
        default=None,
        description="Login page brand image",
        example="https://example.com/brand.png",
    )

    geoip_db_path: str = Field(
        default="/storage/GeoLite2-City.mmdb",
        description="Path to the GeoIP database",
    )

    force_create_admin: bool = Field(
        default=False,
        description="Ensure creation of admin user on first run",
    )

    disable_rest_docs: bool = Field(
        default=False,
        description="Disable REST API documentation",
    )

    audit_trail: bool = Field(
        default=True,
        description="Enable audit trail",
    )

    openapi_include_addon_endpoints: bool = Field(
        default=False,
        description="Include addon endpoints in the OpenAPI schema",
    )

    log_retention_days: int = Field(
        default=7,
        description="Number of days to keep logs in the event log",
    )

    event_retention_days: int | None = Field(
        default=None,
        description="Number of days to keep events in the event log",
        example=90,
    )

    ynput_cloud_api_url: str | None = Field(
        "https://im.ynput.cloud",
        description="YnputConnect URL",
    )

    http_timeout: int = Field(
        default=120,
        description="The default timeout for HTTP requests the server uses "
        "to connect to external services",
    )

    log_file: str | None = Field(
        default=None,
        description="Path to the log file",
        deprecated=True,
    )

    log_mode: Literal["text", "json"] = Field(
        default="text",
        description="Log output format",
    )

    log_level: LogLevel = Field(
        default="DEBUG",
        description="Log level for the console output",
    )

    log_level_db: LogLevel = Field(
        default="INFO",
        description="Log level stored in the event stream",
    )

    @validator("log_level", "log_level_db", pre=True)
    def validate_log_level(cls, value: str) -> str:
        return value.upper()

    # Metrics settings

    metrics_api_key: str | None = Field(
        default=None,
        description="API key allowing access to the system metrics endpoint",
    )

    metrics_send_system: bool = Field(
        default=False,
        description="Send system metrics to Ynput Cloud",
    )

    metrics_send_saturated: bool = Field(
        default=False,
        description="Send saturated metrics to Ynput Cloud",
    )

    # Email settings

    email_from: str = Field("noreply@ynput.cloud", description="Email sender address")
    email_smtp_host: str | None = Field(None, description="SMTP server hostname")
    email_smtp_port: int | None = Field(None, description="SMTP server port")
    email_smtp_tls: bool = Field(False, description="Use SSL for SMTP connection")
    email_smtp_user: str | None = Field(None, description="SMTP server username")
    email_smtp_pass: str | None = Field(None, description="SMTP server password")

    # Project storage

    default_project_storage_type: Literal["local", "s3"] = Field(
        "local",
        description="Default project storage type",
    )

    default_project_storage_root: str = Field(
        default="/storage/server/projects",
        description="Path to the directory containing the project files."
        " such as comment attachments, thumbnails, etc.",
    )

    default_project_storage_bucket_name: str | None = Field(
        default=None,
        description="Default project storage bucket name (S3)",
    )

    default_project_storage_cdn_resolver: str | None = Field(
        default=None,
        description="Project files CDN resolver URL",
    )


#
# Load configuration from environment variables
#


def load_config() -> AyonConfig:
    """Load configuration"""
    prefix = "ayon_"
    env_data = {}
    for key, value in dict(os.environ).items():
        if not key.lower().startswith(prefix):
            continue

        key = key.lower().removeprefix(prefix)
        if key in AyonConfig.__fields__:
            env_data[key] = value

    config = AyonConfig(**env_data)

    if (config.motd) is None and (config.motd_path is not None):
        if os.path.exists(config.motd_path):
            with open(config.motd_path) as motd_file:
                config.motd = motd_file.read()

    return config


ayonconfig = load_config()
