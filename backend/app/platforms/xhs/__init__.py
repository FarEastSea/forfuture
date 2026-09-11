from app.domain.enums import Platform
from app.platforms.base import PlatformPlugin
from app.platforms.xhs.auth import XHSAuthenticator
from app.platforms.xhs.client import XHSFetcher
from app.platforms.xhs.parser import XHSParser


def build_plugin() -> PlatformPlugin:
    return PlatformPlugin(
        name=Platform.XHS,
        parser=XHSParser(),
        make_fetcher=XHSFetcher,
        authenticator=XHSAuthenticator(),
        needs_browser=True,
    )
