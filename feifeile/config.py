"""配置管理模块

通过环境变量或 .env 文件加载所有运行时配置。
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class HNAConfig(BaseSettings):
    """海南航空账户及 API 配置"""

    model_config = SettingsConfigDict(
        env_prefix="HNA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    username: str = Field(..., description="海南航空会员手机号 / 账号")
    password: str = Field(..., description="海南航空账户密码")

    # 可选：如需特定 app 版本或设备指纹，在此覆盖
    app_version: str = Field(default="10.12.0", description="模拟的 App 版本号")
    device_id: str = Field(
        default="feifeile-monitor-001", description="模拟设备 ID"
    )

    # 基础 URL（如官方发布新域名可在此更新）
    base_url: str = Field(
        default="https://app.hnair.com",
        description="海南航空移动 API 基础 URL",
    )
    timeout: float = Field(default=60.0, description="HTTP 请求超时秒数")
    max_retries: int = Field(default=3, description="失败重试次数")

    # HMAC-SHA1 请求签名相关
    certificate_hash: str = Field(
        default="6093941774D84495A5D15D8F909CAA1E",
        description="签名附加参数（拼接到待签字符串末尾）",
    )
    hard_code: str = Field(
        default="21047C596EAD45209346AE29F0350491",
        description="HMAC-SHA1 签名密钥",
    )
    akey: str = Field(
        default="9E4BBDDEC6C8416EA380E418161A7CD3",
        description="应用身份标识",
    )


class WeComConfig(BaseSettings):
    """企业微信机器人配置"""

    model_config = SettingsConfigDict(
        env_prefix="WECOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    webhook_url: str = Field(..., description="企业微信群机器人 Webhook URL")
    timeout: float = Field(default=10.0, description="发送消息超时秒数")


class MonitorConfig(BaseSettings):
    """监控任务配置"""

    model_config = SettingsConfigDict(
        env_prefix="MONITOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 单位：元，低于或等于此价格触发通知
    price_threshold: float = Field(
        default=199.0, description="触发通知的价格阈值（元）"
    )
    interval_hours: float = Field(
        default=4.0, description="两次查询之间的间隔（小时）"
    )
    # 订阅列表存储路径（JSON 文件），默认放在当前目录
    subscriptions_file: str = Field(
        default="subscriptions.json", description="订阅信息持久化文件路径"
    )

    @field_validator("interval_hours")
    @classmethod
    def interval_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("interval_hours 必须大于 0")
        return v

    @field_validator("price_threshold")
    @classmethod
    def threshold_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("price_threshold 必须大于 0")
        return v


class AppConfig(BaseSettings):
    """聚合配置，统一读取所有子配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    hna: HNAConfig = Field(default_factory=HNAConfig)
    wecom: WeComConfig = Field(default_factory=WeComConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
