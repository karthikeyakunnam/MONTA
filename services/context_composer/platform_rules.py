"""
MONTA — Platform Specifications
=================================
Delivery constraints per destination. ``ideal_duration_s`` is the retention
sweet spot the Story Architect targets when the creator gives no duration.
"""

from shared.contracts.context import PlatformSpec
from shared.contracts.vocab import Platform

PLATFORM_SPECS: dict[Platform, PlatformSpec] = {
    Platform.INSTAGRAM: PlatformSpec(platform=Platform.INSTAGRAM, max_duration_s=90, ideal_duration_s=(15, 30),
                                     aspect_ratio="9:16", resolution="1080x1920", short_form=True),
    Platform.TIKTOK: PlatformSpec(platform=Platform.TIKTOK, max_duration_s=180, ideal_duration_s=(15, 34),
                                  aspect_ratio="9:16", resolution="1080x1920", short_form=True),
    Platform.YOUTUBE_SHORT: PlatformSpec(platform=Platform.YOUTUBE_SHORT, max_duration_s=60, ideal_duration_s=(20, 45),
                                         aspect_ratio="9:16", resolution="1080x1920", short_form=True),
    Platform.YOUTUBE: PlatformSpec(platform=Platform.YOUTUBE, max_duration_s=1800, ideal_duration_s=(60, 480),
                                   aspect_ratio="16:9", resolution="1920x1080", short_form=False),
    Platform.GENERAL: PlatformSpec(platform=Platform.GENERAL, max_duration_s=600, ideal_duration_s=(30, 120),
                                   aspect_ratio="16:9", resolution="1920x1080", short_form=False),
}
