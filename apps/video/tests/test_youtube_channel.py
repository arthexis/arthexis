import pytest

from apps.video.models import YoutubeChannel


@pytest.mark.django_db
def test_channel_url_prefers_handle():
    channel = YoutubeChannel.objects.create(
        title="Arthexis",
        channel_id="UC1234abcd",
        handle="@Arthexis",
    )

    assert channel.get_handle() == "Arthexis"
    assert channel.get_handle(include_at=True) == "@Arthexis"
    assert channel.get_channel_url() == "https://www.youtube.com/@Arthexis"


@pytest.mark.django_db
def test_channel_url_falls_back_to_channel_id():
    channel = YoutubeChannel.objects.create(
        title="Fallback Only",
        channel_id="UC5678efgh",
        handle="",
    )

    assert channel.get_channel_url() == "https://www.youtube.com/channel/UC5678efgh"
