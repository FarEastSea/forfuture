from app.domain.enums import Platform
from app.platforms.base import PlatformPlugin
from app.platforms.qq.auth import QQAuthenticator
from app.platforms.qq.client import QQFetcher
from app.platforms.qq.parser import QQParser


def build_plugin() -> PlatformPlugin:
    return PlatformPlugin(
        name=Platform.QQ,
        parser=QQParser(),
        make_fetcher=QQFetcher,
        authenticator=QQAuthenticator(),
        needs_browser=False,
    )
