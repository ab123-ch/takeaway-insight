"""运行配置；平台凭证只经各门店显式配置的环境变量读取。"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAKEAWAY_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/takeaway.db"
    admin_username: str = "admin"
    admin_password: str = ""
    secret_key: str = ""
    mcp_token: str = ""
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    secure_cookies: bool = False
    timezone: str = "Asia/Shanghai"
    public_url: str = "http://127.0.0.1:8765"
    sync_lease_seconds: int = Field(default=300, ge=60)
    agent_project_dir: str = ""
    agent_log_dir: str = "data/agent-runs"
    antigravity_executable: str = "agy"
    dsh_executable: str = "dsh"

    def require_web_secrets(self) -> None:
        if not self.secret_key or len(self.secret_key) < 32:
            raise ValueError("请设置至少32字符的 TAKEAWAY_SECRET_KEY，或运行 takeaway init")
        if len(self.admin_password) < 12:
            raise ValueError("请设置至少12字符的 TAKEAWAY_ADMIN_PASSWORD，或运行 takeaway init")


PACKAGE_DIR = Path(__file__).parent
